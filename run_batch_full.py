"""
Full‑scale Batch API enrichment – resumable, with smaller sub‑batches
for reliable upload.  Uses the same per‑product commit pattern as pipeline.py.
"""
from database import get_db, Product, Enrichment, Run, SessionLocal, engine
from claude_enricher import classify_tier
from scraper import scrape_product, init_scraper
from batch_enricher import submit_batch, poll_batch, download_and_merge

# ── Load the supplier prefix map (same as pipeline.py does at startup) ──
init_scraper()

ANTHROPIC_BATCH_LIMIT = 10000   # max Anthropic accepts per batch
SUBMIT_CHUNK = 2000             # upload in smaller chunks to avoid timeouts

# ── 1. Check for existing partial run ──
with get_db() as db:
    existing_run = db.query(Run).filter_by(status="running", notes="batch_full_with_scrape").first()
    if existing_run:
        run_id = existing_run.id
        already_prepared = db.query(Enrichment).filter_by(run_id=run_id).count()
        print(f"Resuming run {run_id}: {already_prepared} enrichment rows already prepared.")
        done_skus = {e.sku for e in db.query(Enrichment.sku).filter_by(run_id=run_id).all()}
    else:
        run_id = None
        done_skus = set()

# ── 2. Get remaining pending products ──
with get_db() as db:
    already_enriched = {e.sku for e in db.query(Enrichment.sku).filter_by(status="success").all()}
    all_products = db.query(Product).filter(Product.sku.notin_(already_enriched)).all()
    if done_skus:
        all_products = [p for p in all_products if p.sku not in done_skus]
    total = len(all_products)
    print(f"Remaining products to prepare: {total}")

    product_dicts = []
    for p in all_products:
        product_dicts.append({
            "id": p.id,
            "sku": p.sku,
            "vendor": p.vendor or "",
            "title": p.title or "",
            "price": p.price,
            "images": p.images or [],
            "image_count": len(p.images or []),
            "existing_content": p.existing_content or {},
            "barcode": getattr(p, "barcode", "") or "",
            "mpn": getattr(p, "mpn", "") or "",
        })

    if run_id is None:
        run = Run(status="running", notes="batch_full_with_scrape")
        db.add(run)
        db.flush()
        run_id = run.id
        db.commit()

# ── 3. Prepare remaining products (if any) ──
if product_dicts:
    chunks_to_prepare = [product_dicts[i:i + ANTHROPIC_BATCH_LIMIT] for i in range(0, total, ANTHROPIC_BATCH_LIMIT)]
    print(f"Preparing {len(chunks_to_prepare)} chunk(s) of new products...")

    for chunk_idx, chunk in enumerate(chunks_to_prepare, 1):
        print(f"\nPreparing chunk {chunk_idx}/{len(chunks_to_prepare)}: {len(chunk)} products")
        engine.dispose()
        session = SessionLocal()
        try:
            for i, pdict in enumerate(chunk):
                try:
                    supplier_content = scrape_product(
                        pdict["sku"], pdict["vendor"], pdict["title"],
                        mpn=pdict["mpn"]
                    )
                except Exception:
                    supplier_content = {"status": "error", "description": "", "specifications": "", "features": ""}

                has_feed_desc = bool(supplier_content.get("description", "").strip())
                has_brand_url = supplier_content.get("status") in ("success", "cached")
                tier = classify_tier(has_feed_desc, has_brand_url, pdict["image_count"],
                                     existing_content=pdict["existing_content"], sku=pdict["sku"])

                e = Enrichment(run_id=run_id, product_id=pdict["id"], sku=pdict["sku"],
                               status="pending", tier=tier,
                               scrape_status=supplier_content.get("status", ""),
                               scrape_url=supplier_content.get("source_url", ""),
                               scraped_content=supplier_content)
                session.add(e)
                session.flush()
                session.commit()

                if (i+1) % 500 == 0:
                    print(f"  ... prepared {i+1}/{len(chunk)} products")
        except Exception as exc:
            session.rollback()
            raise
        finally:
            session.close()
else:
    print("All products already prepared. Proceeding to submission.")

# ── 4. Submit all prepared enrichments (resume-safe) ──
with get_db() as db:
    pending = db.query(Enrichment).filter_by(run_id=run_id, status="pending").all()
    print(f"\nTotal pending enrichments to submit: {len(pending)}")

    all_items = []
    for e in pending:
        product = db.query(Product).filter_by(id=e.product_id).first()
        if not product:
            continue
        supplier_content = e.scraped_content or {}
        product_data = {
            "id": product.id,
            "sku": product.sku,
            "title": product.title or "",
            "price": product.price,
            "vendor": product.vendor or "",
            "brand": product.vendor or "",
            "images": product.images or [],
            "image_count": len(product.images or []),
            "existing_content": product.existing_content or {},
            "barcode": getattr(product, "barcode", "") or "",
        }
        all_items.append((e.id, product_data, supplier_content, e.tier))

sub_batches = [all_items[i:i + SUBMIT_CHUNK] for i in range(0, len(all_items), SUBMIT_CHUNK)]
print(f"Submitting {len(sub_batches)} sub‑batch(es) of up to {SUBMIT_CHUNK} requests each")

for sub_idx, sub in enumerate(sub_batches, 1):
    print(f"\n{'='*60}")
    print(f"Sub‑batch {sub_idx}/{len(sub_batches)}: {len(sub)} requests")
    print(f"{'='*60}")
    try:
        batch = submit_batch(sub)
        final = poll_batch(batch["id"])
        download_and_merge(final, run_id)
    except Exception as e:
        print(f"Sub‑batch {sub_idx} failed: {e}")
        print("You can re‑run this script to resume from the remaining pending enrichments.")
        break

    with get_db() as db:
        ok = db.query(Enrichment).filter_by(run_id=run_id, status="success").count()
        fail = db.query(Enrichment).filter_by(run_id=run_id, status="failed").count()
        print(f"Progress: {ok} success / {fail} failed so far")

# ── 5. Final summary ──
with get_db() as db:
    total_ok = db.query(Enrichment).filter_by(run_id=run_id, status="success").count()
    total_fail = db.query(Enrichment).filter_by(run_id=run_id, status="failed").count()
    print(f"\n{'='*60}")
    print(f"BATCH RUN COMPLETE")
    print(f"Total: {total_ok} success / {total_fail} failed")
    print(f"Run ID: {run_id}")
    print(f"{'='*60}")
"""
Standalone batch proof — submits a small set of products to Anthropic Batch API,
polls, downloads results, and merges into the database.
"""

import sys
from database import get_db, Product, Enrichment, Run
from claude_enricher import classify_tier
from scraper import scrape_product
from batch_enricher import submit_batch, poll_batch, download_and_merge

LIMIT = 200   # number of products for the proof run

# Create a temporary run record
with get_db() as db:
    run = Run(status="running", notes="batch_proof")
    db.add(run)
    db.flush()
    run_id = run.id

    # Get products that haven't been enriched yet
    already_done = {e.sku for e in db.query(Enrichment.sku).filter_by(status="success").all()}
    products = db.query(Product).filter(Product.sku.notin_(already_done)).limit(LIMIT).all()

    print(f"Batch proof: {len(products)} products, run_id={run_id}")

    items = []
    for p in products:
        # Quick scrape (will hit cache for most, be fast)
        supplier_content = scrape_product(p.sku, p.vendor or "", p.title or "")
        has_feed_desc = bool(supplier_content.get("description", "").strip())
        has_brand_url = supplier_content.get("status") in ("success", "cached")
        image_count = len(p.images or [])
        tier = classify_tier(has_feed_desc, has_brand_url, image_count,
                             existing_content=p.existing_content or {}, sku=p.sku)

        # Pre-create enrichment row
        e = Enrichment(run_id=run_id, product_id=p.id, sku=p.sku, status="pending", tier=tier,
                       scrape_status=supplier_content.get("status", ""),
                       scraped_content=supplier_content)
        db.add(e)
        db.flush()

        product_data = {
            "id": p.id,
            "sku": p.sku,
            "title": p.title,
            "price": p.price,
            "vendor": p.vendor,
            "brand": p.vendor,
            "images": p.images or [],
            "image_count": image_count,
            "existing_content": p.existing_content or {},
            "barcode": getattr(p, "barcode", ""),
        }
        items.append((e.id, product_data, supplier_content, tier))

    db.commit()

# Submit, poll, merge
batch = submit_batch(items)
final = poll_batch(batch["id"])
download_and_merge(final, run_id)

# Quick summary
with get_db() as db:
    ok = db.query(Enrichment).filter_by(run_id=run_id, status="success").count()
    fail = db.query(Enrichment).filter_by(run_id=run_id, status="failed").count()
    print(f"\nBatch proof complete: {ok} success / {fail} failed")
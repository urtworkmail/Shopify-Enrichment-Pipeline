"""
Resume batch submission from run 30 – with smaller sub‑batches and retry logic.
"""
from database import get_db, Product, Enrichment
from batch_enricher import submit_batch, poll_batch, download_and_merge
import time

RUN_ID = 30
SUBMIT_CHUNK = 500       # smaller chunk for reliability
MAX_RETRIES = 3           # retry each sub‑batch on failure

with get_db() as db:
    pending = db.query(Enrichment).filter_by(run_id=RUN_ID, status="pending").all()
    print(f"Pending enrichments to submit: {len(pending)}")

    if not pending:
        print("All done – nothing to submit.")
        exit()

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

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            batch = submit_batch(sub)
            final = poll_batch(batch["id"])
            download_and_merge(final, RUN_ID)
            break   # success – exit retry loop
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt * 5
                print(f"Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print("Max retries reached. Re‑run this script to resume from the remaining pending enrichments.")

    with get_db() as db:
        ok = db.query(Enrichment).filter_by(run_id=RUN_ID, status="success").count()
        fail = db.query(Enrichment).filter_by(run_id=RUN_ID, status="failed").count()
        print(f"Progress: {ok} success / {fail} failed so far")

# Final summary
with get_db() as db:
    ok = db.query(Enrichment).filter_by(run_id=RUN_ID, status="success").count()
    fail = db.query(Enrichment).filter_by(run_id=RUN_ID, status="failed").count()
    print(f"\n{'='*60}")
    print(f"SUBMISSION COMPLETE")
    print(f"Total: {ok} success / {fail} failed")
    print(f"{'='*60}")
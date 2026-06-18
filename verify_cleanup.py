from database import SessionLocal, Enrichment, Product

db = SessionLocal()

total_products = db.query(Product).count()
total_enrichments = db.query(Enrichment).count()
success_count = db.query(Enrichment).filter_by(status='success').count()
pending_wb = db.query(Enrichment).filter_by(status='success', writeback_status='pending').count()
run36_count = db.query(Enrichment).filter_by(run_id=36).count()

# Check for duplicate SKUs
from sqlalchemy import func
dupes = db.query(Enrichment.sku, func.count(Enrichment.id)).group_by(Enrichment.sku).having(func.count(Enrichment.id) > 1).all()

print(f"Products in DB: {total_products}")
print(f"Enrichments after cleanup: {total_enrichments}")
print(f"All status='success': {success_count}")
print(f"All writeback='pending': {pending_wb}")
print(f"All in run 36: {run36_count}")
print(f"Duplicate SKUs: {len(dupes)} (should be 0)")

# Spot check a known product
e = db.query(Enrichment).filter_by(sku='AA700994').first()
if e:
    print(f"\nSpot check AA700994: status={e.status}, run_id={e.run_id}, writeback={e.writeback_status}")

db.close()
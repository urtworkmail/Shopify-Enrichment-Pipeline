from database import SessionLocal, Product, Enrichment

db = SessionLocal()
for sku in ['AA6502', 'CU606322']:
    e = db.query(Enrichment).filter_by(sku=sku, status='success').order_by(Enrichment.created_at.desc()).first()
    p = db.query(Product).filter_by(sku=sku).first()
    if e and p and e.enriched_data:
        p.title = e.enriched_data.get('title', p.title)
        p.vendor = e.enriched_data.get('brand') or p.vendor
        db.commit()
        print(f'{sku}: title updated to "{p.title}"')
db.close()
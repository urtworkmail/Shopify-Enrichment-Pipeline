from database import SessionLocal, Product, Enrichment

sku = 'PEN50-D'   # change to any SKU you want to inspect
db = SessionLocal()

product = db.query(Product).filter_by(sku=sku).first()
if not product:
    print(f'{sku} not found')
    db.close()
    exit()

enrichment = (
    db.query(Enrichment)
    .filter_by(sku=sku, status='success')
    .order_by(Enrichment.created_at.desc())
    .first()
)

if not enrichment or not enrichment.enriched_data:
    print(f'No successful enrichment for {sku}')
    db.close()
    exit()

data = enrichment.enriched_data

print('=== IMAGE ALT TEXTS (should be a list, distinct per image) ===')
alt = data.get('image_alt_texts', 'MISSING')
print(alt)
if isinstance(alt, list):
    print(f'Count: {len(alt)}, Distinct: {len(set(alt))}')

print('\n=== SEO TITLE (should end with | Mega Office Supplies, <=60 chars) ===')
print(data.get('seo_title', 'MISSING'))

print('\n=== IMAGE IDs (first 2, should be MediaImage IDs) ===')
for i, img in enumerate((product.images or [])[:2], 1):
    print(f'  Image {i}: id={img.get("id")}, url={img.get("url","")[:60]}...')

print('\n=== SUPPLIER_CODE (will appear on writeback, not in enriched data) ===')
print('SKU:', sku)
print('Derived supplier_code:', sku[2:] if len(sku) > 2 else sku)

db.close()
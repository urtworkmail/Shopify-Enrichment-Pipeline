from supplier_router import _SUPPLIER_MAP, _MULTI_BRAND_PREFIXES, load_supplier_map
from database import SessionLocal, Product
import csv
from urllib.parse import urlparse

load_supplier_map('prefix_supplier_map_FINAL.csv')

# Get product counts
db = SessionLocal()
product_counts = {}
all_products = db.query(Product.sku).all()
for (sku,) in all_products:
    prefix = sku[:2].upper()
    product_counts[prefix] = product_counts.get(prefix, 0) + 1
db.close()

rows = []
for prefix in sorted(_SUPPLIER_MAP.keys()):
    info = _SUPPLIER_MAP.get(prefix, {})
    supplier_name = info.get('supplier_name', '')
    domains_raw = info.get('supplier_domain', '')

    # Extract primary domain
    domain_list = domains_raw.split('|')
    primary_domain = domain_list[0].split(';')[0].strip() if domain_list else ''
    if primary_domain.startswith('http'):
        primary_domain = urlparse(primary_domain).netloc

    # Check multi-brand
    is_multi = prefix in _MULTI_BRAND_PREFIXES

    # Get product count
    count = product_counts.get(prefix, 0)

    rows.append({
        'prefix': prefix,
        'supplier_name': supplier_name,
        'domain': primary_domain,
        'product_count': count,
        'is_multi_brand': 'YES' if is_multi else 'NO',
        'url_pattern_notes': '',
        'search_operator': '',
        'url_structure': ''
    })

# Sort by product count descending
rows.sort(key=lambda x: x['product_count'], reverse=True)

# Write CSV
with open('output/supplier_manual_assessment.csv', 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['prefix', 'supplier_name', 'domain', 'product_count', 'is_multi_brand', 'url_pattern_notes', 'search_operator', 'url_structure'])
    writer.writeheader()
    writer.writerows(rows)

print(f'Created CSV with {len(rows)} suppliers')
print('\n=== Top 30 by Product Count ===')
for r in rows[:30]:
    print(f"{r['prefix']}: {r['supplier_name'][:30]:<30} | {r['domain']:<35} | {r['product_count']:>5} products | Multi: {r['is_multi_brand']}")
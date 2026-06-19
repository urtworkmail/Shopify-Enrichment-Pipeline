"""
Comprehensive Supplier Scraping Assessment
Tests each supplier with both MPN and Product Name to determine realistic scrape capability.
"""
import csv
import time
from scraper import scrape_product, init_scraper, SUPPLIER_SEARCH_CONFIG
from supplier_router import load_supplier_map, _SUPPLIER_MAP, _IGNORE_PREFIXES, _ACCO_PREFIXES, _MULTI_BRAND_PREFIXES
from database import SessionLocal, Product

# Initialize
init_scraper()

OUTPUT_FILE = "output/supplier_scraping_assessment.csv"

# Get product counts per prefix
db = SessionLocal()
product_counts = {}
all_prefixes = db.query(Product.sku).all()
for (sku,) in all_prefixes:
    prefix = sku[:2].upper()
    product_counts[prefix] = product_counts.get(prefix, 0) + 1
db.close()

print("Product counts loaded")

# Test each supplier
results = []

db = SessionLocal()

# Get all unique prefixes that have products
test_prefixes = []
for prefix in _SUPPLIER_MAP.keys():
    if prefix in _IGNORE_PREFIXES or prefix in _ACCO_PREFIXES:
        continue
    if product_counts.get(prefix, 0) > 0:
        test_prefixes.append(prefix)

print(f"Testing {len(test_prefixes)} suppliers...")

for i, prefix in enumerate(test_prefixes, 1):
    print(f"[{i}/{len(test_prefixes)}] Testing {prefix}...", end=" ", flush=True)

    # Get sample products
    db = SessionLocal()
    products = db.query(Product).filter(Product.sku.like(f'{prefix}%')).limit(3).all()
    db.close()

    if not products:
        print("no products")
        continue

    info = _SUPPLIER_MAP.get(prefix, {})
    domain = info.get('supplier_domain', '').split('|')[0].split(';')[0].strip()
    if domain.startswith('http'):
        from urllib.parse import urlparse
        domain = urlparse(domain).netloc

    supplier_name = info.get('supplier_name', '')
    product_count = product_counts.get(prefix, 0)

    # Test first product with name and MPN
    p = products[0]
    title = p.title or ''
    vendor = p.vendor or ''
    mpn = getattr(p, 'mpn', '') or ''

    # Test with product name
    result_name = scrape_product(p.sku, vendor, title)

    # Test with MPN
    result_mpn = None
    if mpn and len(mpn) > 2:
        result_mpn = scrape_product(p.sku, vendor, title, mpn=mpn)
        time.sleep(0.5)

    # Analyze results
    has_desc_name = bool(result_name.get('description', '').strip())
    has_specs_name = bool(result_name.get('specifications', '').strip())
    has_content_name = has_desc_name or has_specs_name

    has_desc_mpn = bool(result_mpn.get('description', '').strip()) if result_mpn else False
    has_specs_mpn = bool(result_mpn.get('specifications', '').strip()) if result_mpn else False
    has_content_mpn = has_desc_mpn or has_specs_mpn if result_mpn else False

    status_name = result_name.get('status', '')
    status_mpn = result_mpn.get('status', '') if result_mpn else ''
    url_name = result_name.get('source_url', '')[:60] if result_name.get('source_url') else ''
    url_mpn = result_mpn.get('source_url', '')[:60] if result_mpn and result_mpn.get('source_url') else ''

    # Determine verdict
    if prefix in _MULTI_BRAND_PREFIXES:
        verdict = "MARGINAL"
        reason = "Multi-brand - needs brand-specific routing"
    elif status_name in ('success', 'cached') and has_content_name:
        verdict = "WORKS"
        reason = "Search finds products, selectors work"
    elif status_name == 'multi_brand_no_match':
        verdict = "MARGINAL"
        reason = "Brand not found in routing"
    elif status_name == 'not_found':
        verdict = "WON'T WORK"
        reason = "SKU search returns nothing"
    elif status_name == 'no_config':
        verdict = "WON'T WORK"
        reason = "No search config configured"
    elif status_name == 'http_521':
        verdict = "WON'T WORK"
        reason = "Server errors (HTTP 521)"
    elif status_name in ('blocked_403', 'blocked_robots'):
        verdict = "WON'T WORK"
        reason = "Login-gated or blocked"
    elif status_name in ('ignore', 'acco_gated'):
        verdict = "WON'T WORK"
        reason = "Portal login required"
    else:
        verdict = "MARGINAL"
        reason = f"Status: {status_name}"

    print(f"{verdict} - {status_name}")

    results.append({
        'prefix': prefix,
        'supplier': supplier_name,
        'domain': domain,
        'product_count': product_count,
        'test_sku': p.sku,
        'test_title': title[:40],
        'test_mpn': mpn[:20] if mpn else '',
        'search_method': 'name',
        'status': status_name,
        'has_content': has_content_name,
        'url_found': url_name,
        'verdict': verdict,
        'reason': reason
    })

    # Add MPN result if different
    if result_mpn and result_mpn.get('status') != status_name:
        results.append({
            'prefix': prefix,
            'supplier': supplier_name,
            'domain': domain,
            'product_count': product_count,
            'test_sku': p.sku,
            'test_title': title[:40],
            'test_mpn': mpn[:20] if mpn else '',
            'search_method': 'mpn',
            'status': status_mpn,
            'has_content': has_content_mpn,
            'url_found': url_mpn,
            'verdict': '',
            'reason': ''
        })

    time.sleep(0.5)

db.close()

# Save results
with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=[
        'prefix', 'supplier', 'domain', 'product_count', 'test_sku',
        'test_title', 'test_mpn', 'search_method', 'status', 'has_content',
        'url_found', 'verdict', 'reason'
    ])
    writer.writeheader()
    writer.writerows(results)

print(f"\nSaved to {OUTPUT_FILE}")

# Summary
print("\n=== SUMMARY ===")
from collections import Counter
verdict_counts = Counter(r['verdict'] for r in results if r['verdict'])
for v, c in verdict_counts.most_common():
    print(f"{v}: {c}")

# Calculate products by verdict
products_by_verdict = {}
for r in results:
    if r['verdict']:
        v = r['verdict']
        products_by_verdict[v] = products_by_verdict.get(v, 0) + r['product_count']

print("\n=== PRODUCTS BY VERDICT ===")
for v, count in sorted(products_by_verdict.items(), key=lambda x: -x[1]):
    print(f"{v}: {count:,} products")
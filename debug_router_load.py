"""
Debug script to verify router state after init_scraper() is called.
Run this AFTER import and init_scraper() to see what's in memory.
"""
import sys

# First, import and init like the batch script does
from scraper import scrape_product, init_scraper, SUPPLIER_SEARCH_CONFIG
from supplier_router import _SUPPLIER_MAP, _IGNORE_PREFIXES, _MULTI_BRAND_PREFIXES

print("=== DEBUG: Router State After init_scraper() ===")
print(f"\n_SUPPLIER_MAP entries: {len(_SUPPLIER_MAP)}")
print(f"_IGNORE_PREFIXES: {sorted(_IGNORE_PREFIXES)}")
print(f"_MULTI_BRAND_PREFIXES: {sorted(_MULTI_BRAND_PREFIXES)}")

# Show first 10 entries
print(f"\nFirst 10 prefixes in _SUPPLIER_MAP:")
for i, (k, v) in enumerate(list(_SUPPLIER_MAP.items())[:10]):
    print(f"  {k}: {v.get('supplier_domain', 'NO DOMAIN')[:50]}")

# Test routing
print("\n=== Test Routing ===")
test_skus = ["SR100A G6", "CD10210", "BH06101", "3M001", "AD001", "MO001"]
for sku in test_skus:
    from supplier_router import resolve_scrape_url
    url, strategy = resolve_scrape_url(sku, "Test Brand")
    print(f"  {sku}: strategy={strategy}, url={url}")

# Check SUPPLIER_SEARCH_CONFIG
print(f"\n=== SUPPLIER_SEARCH_CONFIG ===")
print(f"Total domains configured: {len(SUPPLIER_SEARCH_CONFIG)}")
print(f"Sample domains: {list(SUPPLIER_SEARCH_CONFIG.keys())[:5]}")

# Summary
print("\n=== SUMMARY ===")
if len(_SUPPLIER_MAP) == 0:
    print("❌ FAIL: _SUPPLIER_MAP is EMPTY - router map did not load!")
    print("   Check that prefix_supplier_map_FINAL.csv exists and is readable")
elif len(_SUPPLIER_MAP) < 50:
    print(f"⚠️  PARTIAL: Only {len(_SUPPLIER_MAP)} prefixes loaded (expected ~71)")
else:
    print(f"✅ OK: {len(_SUPPLIER_MAP)} prefixes loaded")
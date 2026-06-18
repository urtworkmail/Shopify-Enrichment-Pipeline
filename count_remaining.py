from database import SessionLocal, Enrichment
from supplier_router import resolve_scrape_url, load_supplier_map
from scraper import SUPPLIER_SEARCH_CONFIG
from urllib.parse import urlparse

db = SessionLocal()

# All SKUs from run 30 with unknown_prefix / no_search_config
bad = {r[0] for r in db.query(Enrichment.sku).filter(
    Enrichment.run_id == 30,
    Enrichment.scrape_status.in_(['unknown_prefix', 'no_search_config'])
).all()}

# Which already have a successful enrichment anywhere?
already_ok = {r[0] for r in db.query(Enrichment.sku).filter(
    Enrichment.sku.in_(bad),
    Enrichment.status == 'success'
).all()}

still_need = bad - already_ok

print(f'Total affected: {len(bad)}')
print(f'Already fixed (have success): {len(already_ok)}')
print(f'Still need enrichment: {len(still_need)}')

# Ensure router map is loaded
load_supplier_map('prefix_supplier_map_FINAL.csv')

benefit = 0
no_config = 0
for sku in still_need:
    brand = ''
    base_url, strategy = resolve_scrape_url(sku, brand)
    if base_url:
        domain = urlparse(base_url).netloc
        if domain in SUPPLIER_SEARCH_CONFIG:
            benefit += 1
        else:
            no_config += 1
    else:
        no_config += 1

print(f'Would get supplier data: {benefit}')
print(f'No search config (skip): {no_config}')
if benefit > 0:
    cost = benefit * 0.022
    print(f'Estimated cost (Batch API): ${cost:.2f}')
db.close()
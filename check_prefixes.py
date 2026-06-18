import csv

with open('prefix_supplier_map_FINAL.csv', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
    prefixes = {r.get('supplier_prefix', '').strip() for r in rows}

for p in ['SR', 'BH', 'JP', '3M']:
    status = "FOUND" if p in prefixes else "MISSING"
    print(f'{p}: {status}')
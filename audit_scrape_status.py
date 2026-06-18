"""
audit_scrape_status.py — list every prefix that got 'unknown_prefix' in run 30,
cross-referenced with the supplier map CSV.
"""
import csv
from collections import Counter
from database import SessionLocal, Enrichment

# Load the supplier map CSV (all prefixes the client believes are mapped)
mapped_prefixes = {}
with open("prefix_supplier_map_FINAL.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        p = row.get("supplier_prefix", "").strip()
        if p:
            mapped_prefixes[p] = {
                "supplier_name": row.get("supplier_name", "").strip(),
                "supplier_domain": row.get("supplier_domain", "").strip(),
                "strategy": row.get("scrape_strategy", "").strip(),
                "status": row.get("status", "").strip(),
            }

db = SessionLocal()

# Count scrape_status per prefix in run 30
prefix_counts = {}
rows = db.query(Enrichment.sku, Enrichment.scrape_status).filter_by(run_id=30).all()
for sku, status in rows:
    prefix = sku[:2].upper()
    prefix_counts.setdefault(prefix, Counter())[status or "NULL"] += 1

# Focus on prefixes that have at least one "unknown_prefix"
print(f"{'Prefix':6s} {'Total':>6s} {'unknown_prefix':>15s} {'Mapped?':8s} {'Supplier Name'}")
print("-" * 90)
for prefix, counts in sorted(prefix_counts.items()):
    unk = counts.get("unknown_prefix", 0)
    if unk == 0:
        continue
    total = sum(counts.values())
    mapped = "YES" if prefix in mapped_prefixes else "NO"
    name = mapped_prefixes.get(prefix, {}).get("supplier_name", "—")
    print(f"{prefix:6s} {total:>6d} {unk:>15d} {mapped:8s} {name}")

db.close()
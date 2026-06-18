from database import SessionLocal, Enrichment

GENUINE_FAILS = [
    "CD365.V49-27", "CSPRZ104", "DS407825", "EVLSCN41000",
    "EVLWCLPC", "EVTP450PK", "PQ981182", "WE215011",
    "AA2008604", "AA2020225XAU", "AD43371", "AD959419",
    "DS37875", "FXCRT9 C", "FXD-IPLD4P1575 NW/WS",
    "FXS+S2305", "GN46017", "WE574402",
]

db = SessionLocal()

print("=== FAILED enrichments in RUN 30 ===")
failed_in_run = db.query(Enrichment).filter_by(run_id=30, status="failed").all()
print(f"Total failed in run 30: {len(failed_in_run)}")
for e in failed_in_run[:20]:
    print(f"  {e.sku}: {e.error_message}")

print("\n=== GENUINE FAILS: do they have ANY success? ===")
for sku in GENUINE_FAILS:
    success = db.query(Enrichment).filter_by(sku=sku, status="success").first()
    if success:
        print(f"  {sku}: ✅ Has success (run {success.run_id}, tier {success.tier})")
    else:
        print(f"  {sku}: ❌ NO success anywhere")

db.close()
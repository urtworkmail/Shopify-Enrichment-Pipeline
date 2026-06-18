"""
cleanup_db.py – Keep only the LATEST successful enrichment per product.
Consolidate all survivors into a single 'final' run, delete everything else.
"""
from database import SessionLocal, Enrichment, Run, Log
from sqlalchemy import func, delete

db = SessionLocal()

# ── Stats before ───────────────────────────────────────────────────
total_enrichments = db.query(Enrichment).count()
total_runs = db.query(Run).count()
total_logs = db.query(Log).count()
print(f"Before: {total_enrichments} enrichments, {total_runs} runs, {total_logs} logs")

# ── 1. Find the latest successful enrichment id per SKU ──────────
# The subquery returns rows like (max_id,) — only one column.
rows = db.query(
    func.max(Enrichment.id).label("max_id")
).filter(Enrichment.status == "success").group_by(Enrichment.sku).all()

latest_ids = {row[0] for row in rows}     # row[0] is the max id
print(f"Products with at least one success: {len(latest_ids)}")

# ── 2. Delete all enrichments that are NOT in the latest set ─────
all_ids = [r[0] for r in db.query(Enrichment.id).all()]
batch = 10000
deleted = 0
for i in range(0, len(all_ids), batch):
    chunk = all_ids[i:i + batch]
    to_delete = [eid for eid in chunk if eid not in latest_ids]
    if to_delete:
        db.execute(delete(Enrichment).where(Enrichment.id.in_(to_delete)))
        deleted += len(to_delete)
        db.commit()
        print(f"  Deleted {deleted} old enrichments…")

print(f"Total enrichments deleted: {deleted}")

# ── 3. Consolidate survivors into one 'final' run ──────────────────
final_run = Run(status="completed", notes="final_consolidated")
db.add(final_run)
db.flush()

db.query(Enrichment).update(
    {"run_id": final_run.id, "writeback_status": "pending", "writeback_error": ""}
)
db.commit()
print(f"All survivors moved to run {final_run.id}, writeback status reset to 'pending'.")

# ── 4. Delete all logs ─────────────────────────────────────────────
log_count = db.query(Log).count()
db.query(Log).delete()
db.commit()
print(f"Deleted {log_count} log entries.")

# ── 5. Delete runs with zero enrichments ───────────────────────────
empty_runs = db.query(Run).filter(~Run.enrichments.any()).all()
for r in empty_runs:
    db.delete(r)
db.commit()
print(f"Deleted {len(empty_runs)} empty runs.")

# ── Final stats ────────────────────────────────────────────────────
final_enrichments = db.query(Enrichment).count()
final_runs = db.query(Run).count()
print(f"\nAfter: {final_enrichments} enrichments, {final_runs} runs")
print(f"Ready for write‑back. Use run ID: {final_run.id}")
db.close()
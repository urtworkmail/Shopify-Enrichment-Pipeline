"""
Final enrichment report – formatted console output.
"""
from database import SessionLocal, Product, Enrichment
from sqlalchemy import func
from collections import Counter

db = SessionLocal()

# ── Core stats ─────────────────────────────────────────────────────
ok = db.query(Enrichment).filter_by(status='success').count()
fail = db.query(Enrichment).filter_by(status='failed').count()
tokens_in = db.query(func.sum(Enrichment.claude_input_tokens)).filter_by(status='success').scalar() or 0
tokens_out = db.query(func.sum(Enrichment.claude_output_tokens)).filter_by(status='success').scalar() or 0

cost_batch = (tokens_in / 1_000_000 * 3.0) + (tokens_out / 1_000_000 * 15.0)
cost_realtime = cost_batch * 2
avg_cost = cost_batch / ok if ok else 0

# ── Failure analysis ────────────────────────────────────────────────
failed_rows = db.query(Enrichment.sku, Enrichment.error_message).filter_by(status='failed').all()

# Categorise errors
hist_brace = 0          # '\n  "title"' – old prompt crash
max_retries = 0         # Max retries exceeded
json_parse = 0          # JSON parse error
batch_unknown = 0       # Batch API error: unknown
other = 0

unique_fail_skus = set()
fail_detail = []        # (sku, error_summary)
for sku, err in failed_rows:
    unique_fail_skus.add(sku)
    if err and '\\n  "title"' in err:
        hist_brace += 1
        tag = "HISTORICAL (prompt brace crash)"
    elif err and 'Max retries exceeded' in err:
        max_retries += 1
        tag = "SEO length / missing suffix"
    elif err and 'JSON parse error' in err:
        json_parse += 1
        tag = "JSON parse error"
    elif err and 'Batch API error' in err:
        batch_unknown += 1
        tag = "Batch API unknown"
    else:
        other += 1
        tag = err[:80] if err else "No error message"
    fail_detail.append((sku, tag))

# Products with NO enrichment
enriched_skus = {e.sku for e in db.query(Enrichment.sku).all()}
all_skus = {p.sku for p in db.query(Product.sku).all()}
missing_products = sorted(all_skus - enriched_skus)

db.close()

# ── Print report ────────────────────────────────────────────────────
sep = "=" * 62
sub = "-" * 62

print(sep)
print("  MEGA OFFICE SUPPLIES — ENRICHMENT PIPELINE FINAL REPORT")
print(sep)
print()
print(f"  Total products enriched  : {ok + fail:>6}")
print(f"  Successful               : {ok:>6}  ({ok/(ok+fail)*100:.2f}%)")
print(f"  Failed                   : {fail:>6}  ({fail/(ok+fail)*100:.2f}%)")
print()
print(f"  Input tokens             : {tokens_in:>12,}")
print(f"  Output tokens            : {tokens_out:>12,}")
print(f"  Batch API cost           : ${cost_batch:>10,.2f}")
print(f"  Real‑time equivalent     : ${cost_realtime:>10,.2f}")
print(f"  Avg cost per product     : ${avg_cost:>10,.4f}")
print()

print(sub)
print("  FAILURE BREAKDOWN")
print(sub)
print(f"  Historical (old prompt crash, already re‑enriched) : {hist_brace:>4}")
print(f"  SEO length / missing suffix (now auto‑repaired)   : {max_retries:>4}")
print(f"  JSON parse errors (one‑off API glitches)          : {json_parse:>4}")
print(f"  Batch API unknown (can be re‑enriched)            : {batch_unknown:>4}")
print(f"  Other                                             : {other:>4}")
print()

print(sub)
print("  UNIQUE FAILED SKUs (deduplicated across runs)")
print(sub)
unique_sorted = sorted(unique_fail_skus)
for i, sku in enumerate(unique_sorted, 1):
    # pick the most recent error for this sku
    errors_for_sku = [e for s, e in fail_detail if s == sku]
    tag = errors_for_sku[-1] if errors_for_sku else "unknown"
    print(f"  {i:3}. {sku:30s}  {tag}")
print()

print(sub)
print("  PRODUCTS WITH NO ENRICHMENT ROW (never processed)")
print(sub)
if missing_products:
    for i, s in enumerate(missing_products, 1):
        print(f"  {i:4}. {s}")
else:
    print("  None — every product in the database has at least one enrichment row.")
print()

print(sep)
print("  REPORT COMPLETE")
print(sep)
"""
pipeline.py -- V2.1 main orchestrator.

Usage:
    python pipeline.py --limit 100 --skip-writeback       # M1: 100-product test
    python pipeline.py --limit 500                        # M2: 500-product QA
    python pipeline.py                                    # M3: full run
    python pipeline.py --skip-fetch                       # reuse cached Shopify data
    python pipeline.py --writeback-only --resume <id>     # write-back a completed run
    python pipeline.py --resume <id>                      # resume interrupted run
    python pipeline.py --skip-previously-enriched         # only process products never successfully enriched before
"""

import argparse
from datetime import datetime
from pathlib import Path

from config import config
from database import init_db, get_db, Product, Enrichment, Run, Log, get_run_stats, get_previously_enriched_skus
from token_manager import (
    initialise as init_token,
    smoke_test,
    start_background_refresh,
)
from shopify_fetch import fetch_and_merge
from shopify_bulk import run_all_bulk_passes
from claude_enricher import enrich_batch, classify_tier
from scraper import scrape_product, init_scraper


def _log(db, run_id, level, module, message, sku=None):
    db.add(Log(run_id=run_id, level=level, module=module, sku=sku, message=message))
    db.flush()
    print(f"[{level}] [{module}]{f' SKU {sku}:' if sku else ''} {message}")


# ── Phase 1: Enrichment ───────────────────────────────────────────────────────

def run_enrichment_phase(
    run_id: int,
    limit: int = None,
    batch_size: int = 50,
    filter_prefix: str = None,
    skip_previously_enriched: bool = False,
):
    with get_db() as db:
        run = db.query(Run).filter_by(id=run_id).first()

        already_done = set(
            e.sku for e in db.query(Enrichment.sku).filter_by(run_id=run_id).all()
        )

        query = db.query(Product).filter(Product.sku.notin_(already_done))

        if filter_prefix:
            query = query.filter(Product.sku.startswith(filter_prefix))

        if skip_previously_enriched:
            done_skus = get_previously_enriched_skus()
            if done_skus:
                query = query.filter(Product.sku.notin_(done_skus))
                _log(db, run_id, "INFO", "pipeline",
                     f"Skipping {len(done_skus)} previously enriched SKUs")

        if limit:
            query = query.limit(limit)

        products = query.all()

        run.total_products = len(products) + len(already_done)
        _log(db, run_id, "INFO", "pipeline",
             f"Enrichment: {len(products)} products pending, {len(already_done)} already done")

        # Snapshot to dicts so we can use outside DB session
        snapshots = [
            {
                "id": p.id,
                "sku": p.sku,
                "shopify_product_id": p.shopify_product_id,
                "title": p.title,
                "price": p.price,
                "vendor": p.vendor,
                "brand": p.vendor,
                "description_html": p.description_html,
                "tags": p.tags,
                "images": p.images or [],
                "image_count": len(p.images or []),
                "barcode": getattr(p, "barcode", ""),
                "raw_csv_data": p.raw_csv_data or {},
                "existing_content": getattr(p, "existing_content", None) or {},
                "mpn": getattr(p, "mpn", "") or "",
            }
            for p in products
        ]

        # Pre-create enrichment rows so dashboard shows pending immediately
        enrichment_id_map: dict[str, int] = {}
        for snap in snapshots:
            e = Enrichment(
                run_id=run_id,
                product_id=snap["id"],
                sku=snap["sku"],
                status="pending",
            )
            db.add(e)
            db.flush()
            enrichment_id_map[snap["sku"]] = e.id

    total_success = 0
    total_fail = 0

    for batch_start in range(0, len(snapshots), batch_size):
        batch = snapshots[batch_start: batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        print(f"\n[pipeline] Batch {batch_num}/{-(-len(snapshots)//batch_size)}: {len(batch)} products")

        # Build enrichment items: resolve supplier content and tier per product
        # Use a SINGLE session for the whole batch to prevent connection-pool exhaustion.
        items = []
        from database import engine as _db_engine, SessionLocal as _SessionLocal
        _db_engine.dispose()  # release any lingering connections before the batch
        _batch_db = _SessionLocal()
        try:
            for snap in batch:
                sku = snap["sku"]
                brand = snap.get("vendor") or snap.get("brand") or ""

                # ── scrape ──────────────────────────────────────────────────
                supplier_content: dict = {}
                try:
                    csv_data = snap.get("raw_csv_data") or {}
                    if csv_data.get("bigcommerce_description"):
                        supplier_content = {
                            "status": "csv_export",
                            "description": csv_data.get("bigcommerce_description", ""),
                            "specifications": csv_data.get("bigcommerce_specs", ""),
                            "features": csv_data.get("bigcommerce_features", ""),
                        }
                    elif config.SCRAPER_ENABLED:
                        supplier_content = scrape_product(
                            sku, brand, snap.get("title", ""),
                            mpn=snap.get("mpn", "")
                        )
                    else:
                        supplier_content = {"status": "disabled"}

                    e = _batch_db.query(Enrichment).filter_by(
                        id=enrichment_id_map[sku]
                    ).first()
                    if e:
                        e.scrape_status = supplier_content.get("status", "")
                        _batch_db.flush()

                except Exception as exc:
                    print(f"[pipeline] {sku}: ERROR in scrape -- {exc}", flush=True)
                    _batch_db.rollback()
                    supplier_content = {"status": "error"}
                    try:
                        e = _batch_db.query(Enrichment).filter_by(
                            id=enrichment_id_map[sku]
                        ).first()
                        if e:
                            e.scrape_status = "error"
                            e.error_message = str(exc)[:200]
                            _batch_db.flush()
                    except Exception:
                        _batch_db.rollback()

                # ── tier classification ──────────────────────────────────────
                has_feed_desc = bool(
                    supplier_content.get("description", "").strip()
                    or supplier_content.get("features", "").strip()
                )
                has_brand_url = supplier_content.get("status") in (
                    "success", "cached", "csv_export"
                )
                image_count = snap.get("image_count", len(snap.get("images", [])))
                tier = classify_tier(
                    has_feed_desc, has_brand_url, image_count,
                    existing_content=snap.get("existing_content", {})
                )

                try:
                    e = _batch_db.query(Enrichment).filter_by(
                        id=enrichment_id_map[sku]
                    ).first()
                    if e:
                        e.tier = tier
                        _batch_db.flush()
                except Exception as exc:
                    print(f"[pipeline] {sku}: ERROR updating tier -- {exc}", flush=True)
                    _batch_db.rollback()

                # commit after every product so a crash on the next one
                # doesn't lose what we already wrote
                _batch_db.commit()
                items.append((enrichment_id_map[sku], snap, supplier_content, tier))

        except Exception as batch_exc:
            _batch_db.rollback()
            raise
        finally:
            _batch_db.close()

        results = enrich_batch(items, run_id)
        ok = sum(1 for r in results if r["status"] == "success")
        fail = sum(1 for r in results if r["status"] != "success")
        total_success += ok
        total_fail += fail

        with get_db() as db:
            _log(db, run_id, "INFO", "pipeline",
                 f"Batch {batch_num} done: {ok} success / {fail} failed")

    with get_db() as db:
        _log(db, run_id, "INFO", "pipeline",
             f"Enrichment complete: {total_success} success / {total_fail} failed")


# ── Phase 2: Write-back ───────────────────────────────────────────────────────

def run_writeback_phase(run_id: int):
    with get_db() as db:
        pending = db.query(Enrichment).filter_by(
            run_id=run_id, status="success", writeback_status="pending"
        ).count()
        if pending == 0:
            print("[pipeline] No enriched products pending write-back.")
            return
        run = db.query(Run).filter_by(id=run_id).first()
        if run:
            run.writeback_status = "running"
        _log(db, run_id, "INFO", "pipeline",
             f"Write-back phase: {pending} products across 3 bulk passes")

    run_all_bulk_passes(run_id)

    with get_db() as db:
        run = db.query(Run).filter_by(id=run_id).first()
        if run:
            run.writeback_status = "completed"
        _log(db, run_id, "INFO", "pipeline", "Write-back complete.")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(run_id: int):
    with get_db() as db:
        s = get_run_stats(db, run_id)
    print("\n" + "=" * 60)
    print("RUN SUMMARY")
    print("=" * 60)
    print(f"  Run ID:          {s.get('id')}")
    print(f"  Status:          {s.get('status')}")
    print(f"  Total:           {s.get('total_products')}")
    print(f"  Successful:      {s.get('enriched_count')} ({s.get('progress_pct')}%)")
    print(f"  Failed:          {s.get('failed_count')}")
    print(f"  Input tokens:    {s.get('total_input_tokens', 0):,}")
    print(f"  Output tokens:   {s.get('total_output_tokens', 0):,}")
    print(f"  Est. cost (USD): ${s.get('estimated_cost_usd', 0):.4f}")
    print(f"  Write-back:      {s.get('writeback_status')}")
    print("=" * 60 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Mega Office Enrichment Pipeline v2.1")
    parser.add_argument("--filter-prefix", type=str, default=None,
                        help="Only process products whose SKU starts with this prefix (e.g. AA, DS, GN)")
    parser.add_argument("--skip-previously-enriched", action="store_true",
                        help="Skip any SKU that has already been successfully enriched in a previous run")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-writeback", action="store_true")
    parser.add_argument("--writeback-only", action="store_true")
    parser.add_argument("--resume", type=int, default=None)
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("MEGA OFFICE SUPPLIES -- ENRICHMENT PIPELINE v2.1")
    print("=" * 60)

    config.validate()
    Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    init_db()
    init_scraper()
    init_token()
    smoke_test()
    start_background_refresh()

    print(f"\n[pipeline] Dashboard: http://localhost:{config.DASHBOARD_PORT}")

    if args.writeback_only:
        if not args.resume:
            print("[pipeline] --writeback-only requires --resume <run_id>")
            return
        run_writeback_phase(args.resume)
        print_summary(args.resume)
        return

    # Create or resume run
    if args.resume:
        run_id = args.resume
        with get_db() as db:
            run = db.query(Run).filter_by(id=run_id).first()
            if not run:
                print(f"[pipeline] Run {run_id} not found.")
                return
            run.status = "running"
        print(f"[pipeline] Resuming run {run_id}")
    else:
        with get_db() as db:
            run = Run(status="running", started_at=datetime.utcnow())
            db.add(run)
            db.flush()
            run_id = run.id
            db.add(Log(run_id=run_id, level="INFO", module="pipeline",
                       message=f"Run {run_id} started"))
        print(f"[pipeline] Started run {run_id}")

    try:
        if not args.skip_fetch:
            print("\n[pipeline] Phase 1a: Shopify bulk query ...")
            fetch_and_merge(use_cache=False)
        else:
            print("[pipeline] Skipping fetch (--skip-fetch)")
            fetch_and_merge(use_cache=True)

        print("\n[pipeline] Phase 1b: Claude enrichment ...")
        run_enrichment_phase(
            run_id,
            limit=args.limit,
            filter_prefix=args.filter_prefix,
            skip_previously_enriched=args.skip_previously_enriched,
        )

        if not args.skip_writeback:
            print("\n[pipeline] Phase 2: Bulk write-back ...")
            run_writeback_phase(run_id)
        else:
            print("[pipeline] Skipping write-back (--skip-writeback)")

        with get_db() as db:
            run = db.query(Run).filter_by(id=run_id).first()
            if run:
                run.status = "completed"
                run.completed_at = datetime.utcnow()

    except Exception as e:
        with get_db() as db:
            run = db.query(Run).filter_by(id=run_id).first()
            if run:
                run.status = "failed"
            db.add(Log(run_id=run_id, level="ERROR", module="pipeline", message=str(e)))
        print(f"\n[pipeline] FAILED: {e}")
        raise

    print_summary(run_id)
    print("[pipeline] Done.")


if __name__ == "__main__":
    main()
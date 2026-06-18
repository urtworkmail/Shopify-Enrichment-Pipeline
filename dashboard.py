"""
dashboard.py -- FastAPI web dashboard with pagination, monochrome design,
               live-run stats, per‑product actions, side panel, and SKU search.
"""

from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from database import get_db, Run, Enrichment, Product, Log, get_run_stats
from config import config

app = FastAPI(title="Mega Enrichment Dashboard", version="2.5")

import uvicorn
from starlette.responses import Response, StreamingResponse
app.state.max_response_size = 20_000_000  # 20 MB

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PAGE_SIZE = 500


def _get_or_create_manual_run(db) -> int:
    manual = db.query(Run).filter_by(status="manual").first()
    if not manual:
        manual = Run(status="manual", started_at=datetime.utcnow(), total_products=0)
        db.add(manual)
        db.flush()
    return manual.id


def _latest_scrape_status(db, sku: str):
    """Return the most recent non-'unknown_prefix' scrape status for a SKU."""
    enrichment = (
        db.query(Enrichment)
        .filter(Enrichment.sku == sku, Enrichment.scrape_status != "unknown_prefix")
        .order_by(Enrichment.created_at.desc())
        .first()
    )
    return enrichment.scrape_status if enrichment else None


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/runs")
def get_runs():
    with get_db() as db:
        from sqlalchemy import select
        active_ids = select(Enrichment.run_id).distinct().subquery()
        runs = (
            db.query(Run)
            .filter(Run.id.in_(active_ids))
            .order_by(Run.started_at.desc())
            .limit(30)
            .all()
        )
        return [get_run_stats(db, r.id) for r in runs]


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    with get_db() as db:
        return get_run_stats(db, run_id)


@app.get("/api/runs/{run_id}/products")
def get_run_products(run_id: int, status: Optional[str] = None,
                     limit: int = PAGE_SIZE, offset: int = 0):
    with get_db() as db:
        q = db.query(Enrichment).filter_by(run_id=run_id)
        if status:
            q = q.filter_by(status=status)
        total = q.count()
        enrichments = q.order_by(Enrichment.updated_at.desc()).offset(offset).limit(limit).all()

        rows = []
        for e in enrichments:
            product = db.query(Product).filter_by(id=e.product_id).first()
            rows.append({
                "sku": e.sku,
                "title": product.title if product else "",
                "tier": e.tier,
                "status": e.status,
                "scrape_status": _latest_scrape_status(db, e.sku) or e.scrape_status,
                "writeback_status": e.writeback_status,
                "cost_usd": round(e.cost_usd or 0, 5),
                "input_tokens": e.claude_input_tokens,
                "output_tokens": e.claude_output_tokens,
                "retry_count": e.retry_count,
                "needs_manual_review": e.needs_manual_review,
                "error": e.error_message or "",
                "updated_at": e.updated_at.isoformat() if e.updated_at else "",
            })

        return {"total": total, "items": rows, "limit": limit, "offset": offset}


@app.get("/api/runs/{run_id}/logs")
def get_logs(run_id: int, level: Optional[str] = None, limit: int = 200):
    with get_db() as db:
        q = db.query(Log).filter_by(run_id=run_id)
        if level:
            q = q.filter_by(level=level)
        logs = q.order_by(Log.timestamp.desc()).limit(limit).all()
        return [
            {
                "timestamp": l.timestamp.isoformat(),
                "level": l.level,
                "module": l.module,
                "sku": l.sku,
                "message": l.message,
            }
            for l in logs
        ]


@app.get("/api/stats")
def get_overall_stats():
    with get_db() as db:
        from sqlalchemy import func
        total_runs = db.query(Run).count()
        total_products = db.query(Product).count()
        total_enriched = db.query(Enrichment).filter_by(status="success").count()
        cost_result = db.query(func.sum(Enrichment.cost_usd)).filter_by(status="success").scalar()
        return {
            "total_runs": total_runs,
            "total_products": total_products,
            "total_enriched": total_enriched,
            "total_cost_usd": round(cost_result or 0, 4),
        }


@app.get("/api/products/{sku}/details")
def get_product_details(sku: str):
    with get_db() as db:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            return JSONResponse({"error": "SKU not found"}, status_code=404)

        enrichment = (
            db.query(Enrichment)
            .filter_by(sku=sku)
            .order_by(Enrichment.created_at.desc())
            .first()
        )

        shopify_data = {
            "title": product.title,
            "vendor": product.vendor,
            "description_html": product.description_html,
            "tags": product.tags,
            "images": product.images or [],
            "existing_content": product.existing_content or {},
            "metafields": product.metafields or [],
        }

        scraped_content = enrichment.scraped_content if enrichment else None
        enriched_data = enrichment.enriched_data if enrichment else None
        writeback_info = {
            "status": enrichment.writeback_status if enrichment else "pending",
            "error": enrichment.writeback_error if enrichment else "",
        }

        return {
            "sku": sku,
            "shopify_product_id": product.shopify_product_id,
            "tier": enrichment.tier if enrichment else None,
            "enrichment_status": enrichment.status if enrichment else "not_started",
            "cost_usd": round(enrichment.cost_usd or 0, 5) if enrichment else 0,
            "shopify_data": shopify_data,
            "scraped_content": scraped_content,
            "enriched_data": enriched_data,
            "writeback": writeback_info,
        }


# ── Per-product action endpoints ──────────────────────────────────────────────

@app.post("/api/products/{sku}/scrape")
def rescrape_product(sku: str, custom_url: Optional[str] = None):
    from scraper import scrape_product, scrape_url

    with get_db() as db:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            return JSONResponse({"error": "SKU not found"}, status_code=404)
        product_id = product.id
        vendor = product.vendor or ""
        title = product.title or ""
        mpn = getattr(product, "mpn", "") or ""

    if custom_url:
        result = scrape_url(custom_url, sku=sku)
        supplier_content = {
            "status": result.get("status", "error"),
            "description": result.get("description", ""),
            "specifications": result.get("specifications", ""),
            "features": result.get("features", ""),
        }
    else:
        supplier_content = scrape_product(sku, vendor, title, mpn=mpn)

    for attempt in range(1, 4):
        try:
            with get_db() as db:
                enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
                manual_run_id = _get_or_create_manual_run(db)
                if not enrichment:
                    enrichment = Enrichment(run_id=manual_run_id, product_id=product_id, sku=sku, status="pending")
                    db.add(enrichment)
                    db.flush()
                enrichment.scrape_status = supplier_content.get("status", "manual")
                enrichment.scraped_content = supplier_content
                db.commit()
            return {"status": "ok", "sku": sku, "scrape_status": enrichment.scrape_status}
        except Exception as db_exc:
            print(f"[dashboard] Scrape DB commit attempt {attempt} failed: {db_exc}", flush=True)
            if attempt == 3:
                return JSONResponse({"status": "error", "sku": sku, "error": "DB write failed"}, status_code=500)
            import time
            time.sleep(0.5)


@app.post("/api/products/{sku}/enrich")
def reenrich_product(sku: str):
    import anthropic, httpx
    from claude_enricher import (
        classify_tier, _load_prompt, _build_user_message, _max_tokens, _cost_usd,
    )
    from validator import validate_claude_response
    from scraper import scrape_product

    with get_db() as db:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            return JSONResponse({"error": "SKU not found"}, status_code=404)
        product_id = product.id
        vendor = product.vendor or ""
        title = product.title or ""
        price = product.price or 0
        barcode = getattr(product, "barcode", "") or ""
        existing = product.existing_content or {}
        mpn = getattr(product, "mpn", "") or ""
        image_count = len(product.images or [])
        enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
        has_scrape = enrichment and enrichment.scraped_content and enrichment.scraped_content.get("status")
        scraped = dict(enrichment.scraped_content) if (enrichment and enrichment.scraped_content) else {}

    if not has_scrape:
        scraped = scrape_product(sku, vendor, title, mpn=mpn)

    has_feed = bool((scraped.get("description") or "").strip())
    has_url = scraped.get("status") in ("success", "cached", "csv_export")
    tier = classify_tier(has_feed, has_url, image_count, existing_content=existing, sku=sku)

    pd = {
        "title": title, "brand": vendor, "vendor": vendor,
        "sku": sku, "price": price, "barcode": barcode,
        "existing_content": existing, "category_path": "Office Supplies",
        "compatible_with": "Not specified.", "rrp": "", "gtin": barcode,
        "image_count": image_count,
    }

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                 timeout=httpx.Timeout(60.0, connect=10.0))
    system = _load_prompt("system.txt")
    try:
        resp = client.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=_max_tokens(tier),
            system=system, messages=[{"role": "user", "content": _build_user_message(pd, scraped, tier)}],
        )
        raw = resp.content[0].text
        it, ot = resp.usage.input_tokens, resp.usage.output_tokens
        valid, parsed, err = validate_claude_response(raw, tier, image_count=image_count)
    except Exception as e:
        with get_db() as db:
            enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
            if enrichment:
                enrichment.status = "failed"
                enrichment.error_message = str(e)[:200]
                enrichment.retry_count = 3
                db.commit()
        return JSONResponse({"status": "failed", "sku": sku, "error": str(e)[:200]}, status_code=500)

    last_error = ""
    for attempt in range(1, 4):
        try:
            with get_db() as db:
                enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
                manual_run_id = _get_or_create_manual_run(db)
                if not enrichment:
                    enrichment = Enrichment(run_id=manual_run_id, product_id=product_id, sku=sku, status="pending")
                    db.add(enrichment)
                    db.flush()

                enrichment.scrape_status = scraped.get("status", "manual")
                enrichment.scraped_content = scraped
                if valid:
                    enrichment.status = "success"
                    enrichment.tier = tier
                    enrichment.enriched_data = parsed
                    enrichment.claude_input_tokens = it
                    enrichment.claude_output_tokens = ot
                    enrichment.cost_usd = _cost_usd(it, ot)
                    enrichment.error_message = ""
                    enrichment.retry_count = 0
                else:
                    enrichment.status = "failed"
                    enrichment.tier = tier
                    enrichment.error_message = err or "Validation failed"
                    enrichment.retry_count = 1

                db.commit()
            return {
                "status": enrichment.status, "sku": sku, "tier": tier,
                "cost_usd": round(enrichment.cost_usd or 0, 5),
                "input_tokens": enrichment.claude_input_tokens,
                "output_tokens": enrichment.claude_output_tokens,
                "error": enrichment.error_message or "",
            }
        except Exception as db_exc:
            last_error = str(db_exc)
            print(f"[dashboard] DB commit attempt {attempt} failed for {sku}: {db_exc}", flush=True)
            if attempt < 3:
                import time
                time.sleep(0.5)

    return JSONResponse({"status": "failed", "sku": sku, "error": f"DB write failed: {last_error[:100]}"}, status_code=500)


@app.post("/api/products/{sku}/writeback")
def writeback_product(sku: str):
    import requests as _requests
    from token_manager import get_headers
    from shopify_bulk import PRODUCT_UPDATE_MUTATION, METAFIELDS_SET_MUTATION
    from validator import prepare_metafields

    def _safe_errors(resp):
        try:
            body = resp.json()
        except Exception:
            return [f"Invalid JSON: {resp.text[:200]}"]
        data = body.get("data") or {}
        for key in data:
            mutation_result = data[key]
            if mutation_result is None:
                return [f"{key} returned null"]
            return mutation_result.get("userErrors", [])
        return ["No mutation result found"]

    with get_db() as db:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            return JSONResponse({"error": "SKU not found"}, status_code=404)

        enrichment = (
            db.query(Enrichment)
            .filter_by(sku=sku, status="success")
            .order_by(Enrichment.created_at.desc())
            .first()
        )
        if not enrichment:
            return JSONResponse({"error": "No successful enrichment found for this SKU"}, status_code=400)

        enriched = enrichment.enriched_data or {}
        headers = get_headers()
        errors = []
        passes_ok = []

        # Pass A: productUpdate
        try:
            resp = _requests.post(
                config.shopify_graphql_url, headers=headers,
                json={"query": PRODUCT_UPDATE_MUTATION, "variables": {"product": {
                    "id": product.shopify_product_id,
                    "title": enriched.get("title", product.title or ""),
                    "descriptionHtml": enriched.get("body_html", ""),
                    "vendor": product.vendor or "",
                    "tags": enriched.get("tags", []),
                    "seo": {"title": enriched.get("seo_title", ""),
                            "description": enriched.get("seo_description", "")},
                }}},
                timeout=30,
            )
            errs = _safe_errors(resp)
            if errs:
                errors.extend([f"productUpdate: {e['message']}" for e in errs if isinstance(e, dict)])
            else:
                passes_ok.append("productUpdate")
        except Exception as e:
            errors.append(f"productUpdate exception: {str(e)[:120]}")

        # Pass B: metafieldsSet
        metafields = prepare_metafields(enriched, sku=sku)
        if metafields:
            for mf in metafields:
                mf["ownerId"] = product.shopify_product_id
            try:
                resp = _requests.post(
                    config.shopify_graphql_url, headers=headers,
                    json={"query": METAFIELDS_SET_MUTATION, "variables": {"metafields": metafields}},
                    timeout=30,
                )
                errs = _safe_errors(resp)
                if errs:
                    errors.extend([f"metafieldsSet: {e['message']}" for e in errs if isinstance(e, dict)])
                else:
                    passes_ok.append("metafieldsSet")
            except Exception as e:
                errors.append(f"metafieldsSet exception: {str(e)[:120]}")

        passes_ok.append("fileUpdate (skipped)")

        try:
            if errors:
                enrichment.writeback_status = "failed"
                enrichment.writeback_error = "; ".join(errors)
            else:
                enrichment.writeback_status = "success"
                enrichment.writeback_error = ""
            db.commit()
        except Exception as db_exc:
            print(f"[dashboard] DB commit failed (Shopify write was ok): {db_exc}", flush=True)

        if errors:
            return JSONResponse({"status": "failed", "sku": sku, "errors": errors}, status_code=500)

        return {"status": "success", "sku": sku, "passes_completed": passes_ok}


@app.get("/favicon.ico")
def favicon():
    return JSONResponse({}, status_code=204)


from fastapi.responses import FileResponse

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return FileResponse("dashboard.html", media_type="text/html")

@app.get("/static/dashboard.css")
def css():
    return FileResponse("dashboard.css", media_type="text/css")

@app.get("/static/dashboard.js")
def js():
    return FileResponse("dashboard.js", media_type="application/javascript")

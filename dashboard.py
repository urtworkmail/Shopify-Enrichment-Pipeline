"""
dashboard.py -- FastAPI web dashboard with pagination and monochrome base design.
"""

from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from database import get_db, Run, Enrichment, Product, Log, get_run_stats
from config import config

app = FastAPI(title="Mega Enrichment Dashboard", version="2.5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PAGE_SIZE = 500   # products per page


def _get_or_create_manual_run(db) -> int:
    manual = db.query(Run).filter_by(status="manual").first()
    if not manual:
        manual = Run(status="manual", started_at=datetime.utcnow(), total_products=0)
        db.add(manual)
        db.flush()
    return manual.id


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/runs")
def get_runs():
    with get_db() as db:
        from sqlalchemy import func
        # Only return runs that actually have enrichments
        runs = (
            db.query(Run)
            .filter(Run.id.in_(
                db.query(Enrichment.run_id).distinct()
            ))
            .order_by(Run.started_at.desc())
            .limit(20)
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
                "scrape_status": e.scrape_status,
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
        total_runs = db.query(Run).count()
        total_products = db.query(Product).count()
        total_enriched = db.query(Enrichment).filter_by(status="success").count()
        from sqlalchemy import func
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


# ── Per-product actions ───────────────────────────────────────────────────────

@app.post("/api/products/{sku}/scrape")
def rescrape_product(sku: str, custom_url: Optional[str] = None):
    from scraper import scrape_product, scrape_url
    with get_db() as db:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            return JSONResponse({"error": "SKU not found"}, status_code=404)
        manual_run_id = _get_or_create_manual_run(db)
        if custom_url:
            result = scrape_url(custom_url, sku=sku)
            supplier_content = {
                "status": result.get("status", "error"),
                "description": result.get("description", ""),
                "specifications": result.get("specifications", ""),
                "features": result.get("features", ""),
            }
        else:
            mpn = getattr(product, "mpn", "") or ""
            supplier_content = scrape_product(sku, product.vendor or "", product.title or "", mpn=mpn)

        enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
        if not enrichment:
            enrichment = Enrichment(run_id=manual_run_id, product_id=product.id, sku=sku, status="pending")
            db.add(enrichment)
            db.flush()
        enrichment.scrape_status = supplier_content.get("status", "manual")
        enrichment.scraped_content = supplier_content
        db.commit()
        return {"status": "ok", "sku": sku, "scrape_status": enrichment.scrape_status}


@app.post("/api/products/{sku}/enrich")
def reenrich_product(sku: str):
    import anthropic, httpx
    from claude_enricher import classify_tier, _load_prompt, _build_user_message, _max_tokens, _cost_usd
    from validator import validate_claude_response
    from scraper import scrape_product

    with get_db() as db:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            return JSONResponse({"error": "SKU not found"}, status_code=404)
        manual_run_id = _get_or_create_manual_run(db)
        enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
        if not enrichment:
            enrichment = Enrichment(run_id=manual_run_id, product_id=product.id, sku=sku, status="pending")
            db.add(enrichment)
            db.flush()

        supplier_content = enrichment.scraped_content or {}
        if not supplier_content or not supplier_content.get("status"):
            mpn = getattr(product, "mpn", "") or ""
            supplier_content = scrape_product(sku, product.vendor or "", product.title or "", mpn=mpn)
            enrichment.scrape_status = supplier_content.get("status", "manual")
            enrichment.scraped_content = supplier_content
            db.flush()

        has_feed_desc = bool(supplier_content.get("description", "").strip())
        has_brand_url = supplier_content.get("status") in ("success", "cached", "csv_export")
        image_count = len(product.images or [])
        tier = classify_tier(has_feed_desc, has_brand_url, image_count,
                             existing_content=product.existing_content or {}, sku=sku)

        product_data = {
            "title": product.title or "", "brand": product.vendor or "", "vendor": product.vendor or "",
            "sku": product.sku, "price": product.price or "",
            "barcode": getattr(product, "barcode", "") or "",
            "existing_content": product.existing_content or {},
            "category_path": "Office Supplies", "compatible_with": "Not specified.",
            "rrp": getattr(product, "rrp", "") or "", "gtin": getattr(product, "barcode", "") or "",
        }

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=httpx.Timeout(60.0, connect=10.0))
        system_prompt = _load_prompt("system.txt")
        user_message = _build_user_message(product_data, supplier_content, tier)

        try:
            response = client.messages.create(
                model=config.CLAUDE_MODEL, max_tokens=_max_tokens(tier),
                system=system_prompt, messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text
            it, ot = response.usage.input_tokens, response.usage.output_tokens
            is_valid, parsed, error = validate_claude_response(raw, tier, image_count=image_count)
            if is_valid:
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
                enrichment.error_message = error or "Validation failed"
                enrichment.retry_count = 1
            db.commit()
            return {
                "status": enrichment.status, "sku": sku, "tier": tier,
                "cost_usd": round(enrichment.cost_usd or 0, 5),
                "input_tokens": enrichment.claude_input_tokens,
                "output_tokens": enrichment.claude_output_tokens,
                "error": enrichment.error_message or "",
            }
        except Exception as e:
            db.rollback()
            enrichment.status = "failed"
            enrichment.error_message = str(e)[:200]
            enrichment.retry_count = 3
            db.commit()
            return JSONResponse({"status": "failed", "sku": sku, "error": str(e)[:200]}, status_code=500)


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

        enrichment = db.query(Enrichment).filter_by(sku=sku, status="success").order_by(Enrichment.created_at.desc()).first()
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
                    "seo": {"title": enriched.get("seo_title", ""), "description": enriched.get("seo_description", "")},
                }}},
                timeout=30,
            )
            errs = _safe_errors(resp)
            if errs: errors.extend([f"productUpdate: {e['message']}" for e in errs if isinstance(e, dict)])
            else: passes_ok.append("productUpdate")
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
                if errs: errors.extend([f"metafieldsSet: {e['message']}" for e in errs if isinstance(e, dict)])
                else: passes_ok.append("metafieldsSet")
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


# ── HTML Dashboard (monochrome base, pagination) ─────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mega Office Enrichment Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0a0a0a; color: #ccc; min-height: 100vh; }
  .header { background: #111; border-bottom: 1px solid #333; padding: 16px 24px;
            display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 18px; font-weight: 600; color: #fff; }
  .header .badge { background: #222; color: #aaa; padding: 3px 10px;
                   border-radius: 999px; font-size: 12px; font-weight: 500; border: 1px solid #444; }
  .main { padding: 24px; max-width: 1600px; margin: 0 auto; }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 16px; margin-bottom: 24px; }
  .stat-card { background: #111; border: 1px solid #333; border-radius: 10px; padding: 20px; }
  .stat-card .label { font-size: 12px; color: #888; text-transform: uppercase;
                      letter-spacing: 0.05em; margin-bottom: 8px; }
  .stat-card .value { font-size: 28px; font-weight: 700; color: #fff; }
  .stat-card .sub { font-size: 13px; color: #888; margin-top: 4px; }
  .progress-bar { background: #222; border-radius: 999px; height: 8px; margin: 12px 0; overflow: hidden; }
  .progress-fill { height: 100%; border-radius: 999px; background: #555; transition: width 0.5s ease; }
  .section { background: #111; border: 1px solid #333; border-radius: 10px;
             padding: 20px; margin-bottom: 20px; }
  .section h2 { font-size: 15px; font-weight: 600; color: #fff; margin-bottom: 16px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 8px; border-bottom: 1px solid #333;
       color: #888; font-weight: 500; text-transform: uppercase; font-size: 11px; }
  td { padding: 8px; border-bottom: 1px solid #1a1a1a; color: #bbb; }
  tr { cursor: pointer; }
  tr:hover td { background: #1a1a1a; }
  .badge-success { background: #0f2b1a; color: #4ade80; padding: 2px 8px;
                   border-radius: 4px; font-size: 11px; font-weight: 500; border: 1px solid #166534; }
  .badge-failed  { background: #2b0f0f; color: #f87171; padding: 2px 8px;
                   border-radius: 4px; font-size: 11px; font-weight: 500; border: 1px solid #991b1b; }
  .badge-pending { background: #1f1a0f; color: #facc15; padding: 2px 8px;
                   border-radius: 4px; font-size: 11px; font-weight: 500; border: 1px solid #854d0e; }
  .badge-running { background: #0f1b2b; color: #60a5fa; padding: 2px 8px;
                   border-radius: 4px; font-size: 11px; font-weight: 500; border: 1px solid #1e3a5f; }
  .tier-1 { color: #e6a817; font-weight: 600; }
  .tier-2 { color: #667eea; font-weight: 600; }
  .tier-3 { color: #888; font-weight: 600; }
  .log-entry { font-family: monospace; font-size: 12px; padding: 6px 10px;
               border-bottom: 1px solid #1a1a1a; display: flex; gap: 12px; }
  .log-time { color: #555; min-width: 160px; }
  .log-level-INFO { color: #60a5fa; min-width: 60px; }
  .log-level-ERROR { color: #f87171; min-width: 60px; }
  .log-level-WARNING { color: #facc15; min-width: 60px; }
  .tabs { display: flex; gap: 4px; margin-bottom: 16px; }
  .tab { padding: 7px 16px; border-radius: 6px; font-size: 13px; cursor: pointer;
         border: 1px solid #333; background: none; color: #888; transition: all 0.15s; }
  .tab.active { background: #555; border-color: #555; color: #fff; font-weight: 500; }
  .tab:hover:not(.active) { color: #ccc; background: #1a1a1a; }
  .run-selector { margin-bottom: 20px; display: flex; align-items: center; gap: 12px; }
  select { background: #111; border: 1px solid #333; color: #ccc; padding: 7px 12px;
           border-radius: 6px; font-size: 13px; cursor: pointer; }
  .refresh-btn { background: #222; border: 1px solid #444; color: #ccc; padding: 7px 14px;
                 border-radius: 6px; cursor: pointer; font-size: 13px; }
  .refresh-btn:hover { background: #333; }
  #auto-refresh { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #888; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #4ade80; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .cost-highlight { color: #ccc; font-weight: 600; }
  .empty-state { text-align: center; padding: 40px; color: #555; font-size: 14px; }
  .action-btn { background: #222; border: 1px solid #444; color: #ccc; padding: 4px 8px;
                border-radius: 4px; cursor: pointer; font-size: 13px; margin-right: 2px; transition: all 0.15s; }
  .action-btn:hover { background: #333; color: #fff; }
  .action-btn.scrape { color: #60a5fa; }
  .action-btn.enrich { color: #facc15; }
  .action-btn.writeback { color: #4ade80; }

  .pagination { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 16px; }
  .pagination button { background: #222; border: 1px solid #444; color: #ccc; padding: 6px 14px;
                       border-radius: 4px; cursor: pointer; font-size: 13px; }
  .pagination button:hover:not(:disabled) { background: #333; }
  .pagination button:disabled { opacity: 0.4; cursor: default; }
  .pagination .page-info { font-size: 13px; color: #888; }

  /* Side panel (unchanged) */
  .overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%;
             background: rgba(0,0,0,0.5); z-index: 999; display: none; }
  .overlay.show { display: block; }
  .side-panel { position: fixed; top: 0; right: -580px; width: 560px; height: 100vh;
                background: #0a0a0a; border-left: 1px solid #333; z-index: 1000;
                transition: right 0.35s ease; display: flex; flex-direction: column; }
  .side-panel.open { right: 0; }
  .panel-header { padding: 18px 20px; border-bottom: 1px solid #333;
                  display: flex; align-items: center; justify-content: space-between; }
  .panel-header h2 { font-size: 16px; font-weight: 600; color: #fff; }
  .panel-header .close-btn { background: none; border: none; color: #888; font-size: 22px; cursor: pointer; }
  .panel-status-bar { height: 4px; background: #555; }
  .panel-status-bar.success { background: #4ade80; }
  .panel-status-bar.failed  { background: #f87171; }
  .panel-status-bar.pending { background: #facc15; }
  .panel-body { flex: 1; overflow-y: auto; padding: 16px 20px; }
  .accordion { border: 1px solid #333; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }
  .accordion-header { background: #111; padding: 12px 16px; cursor: pointer;
                      display: flex; align-items: center; justify-content: space-between;
                      font-size: 13px; font-weight: 600; color: #aaa; text-transform: uppercase;
                      border: none; width: 100%; text-align: left; }
  .accordion-header:hover { background: #1a1a1a; }
  .accordion-body { background: #0a0a0a; padding: 0 16px; max-height: 0;
                    overflow: hidden; transition: max-height 0.3s ease, padding 0.3s ease; }
  .accordion.open .accordion-body { max-height: 600px; padding: 12px 16px; overflow-y: auto; }
  .field-row { display: flex; margin-bottom: 6px; font-size: 13px; }
  .field-label { color: #888; min-width: 120px; font-family: monospace; font-size: 12px; }
  .field-value { color: #ccc; word-break: break-word; }
  pre { background: #050505; padding: 12px; border-radius: 6px; font-size: 12px;
        overflow-x: auto; white-space: pre-wrap; word-break: break-word; color: #aaa; border: 1px solid #1a1a1a; }
  .alt-list { list-style: none; padding: 0; }
  .alt-list li { padding: 6px 0; border-bottom: 1px solid #1a1a1a; font-size: 13px; }
  .alt-list .alt-num { color: #555; margin-right: 8px; }
</style>
</head>
<body>
<div class="header">
  <h1>Mega Office Enrichment</h1>
  <span class="badge" id="run-status-badge">Loading...</span>
  <div style="margin-left:auto; display:flex; align-items:center; gap:12px;">
    <div id="auto-refresh"><div class="dot"></div> Auto-refresh 5s</div>
    <button class="refresh-btn" onclick="loadAll()">Refresh now</button>
  </div>
</div>

<div class="overlay" id="overlay" onclick="closePanel()"></div>

<div class="main">
  <div class="run-selector">
    <label style="font-size:13px;color:#888;">Run:</label>
    <select id="run-select" onchange="onRunChange()"><option>Loading...</option></select>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="label">Progress</div>
      <div class="value" id="stat-progress">0%</div>
      <div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%"></div></div>
      <div class="sub" id="stat-counts">0 / 0 products</div>
    </div>
    <div class="stat-card">
      <div class="label">Successful</div>
      <div class="value" id="stat-success" style="color:#4ade80">0</div>
    </div>
    <div class="stat-card">
      <div class="label">Failed</div>
      <div class="value" id="stat-failed" style="color:#f87171">0</div>
    </div>
    <div class="stat-card">
      <div class="label">Est. API Cost</div>
      <div class="value cost-highlight" id="stat-cost">$0.00</div>
      <div class="sub" id="stat-tokens"></div>
    </div>
    <div class="stat-card">
      <div class="label">Write-back</div>
      <div class="value" id="stat-writeback" style="font-size:18px">pending</div>
    </div>
  </div>

  <div class="section">
    <div class="tabs">
      <button class="tab active" onclick="switchTab('products','all')">All Products</button>
      <button class="tab" onclick="switchTab('products','success')">Successful</button>
      <button class="tab" onclick="switchTab('products','failed')">Failed</button>
      <button class="tab" onclick="switchTab('logs','')">Logs</button>
    </div>

    <div id="products-panel">
      <table><thead><tr>
        <th>SKU</th><th>Title</th><th>Tier</th><th>Status</th>
        <th>Scrape</th><th>Write-back</th><th>Tokens in</th>
        <th>Tokens out</th><th>Cost</th><th>Retries</th><th>Actions</th>
      </tr></thead>
      <tbody id="products-tbody"></tbody></table>
      <div class="pagination" id="pagination"></div>
    </div>

    <div id="logs-panel" style="display:none;">
      <div id="logs-container"></div>
    </div>
  </div>
</div>

<div class="side-panel" id="side-panel">
  <div class="panel-header">
    <h2 id="panel-title">Product Details</h2>
    <button class="close-btn" onclick="closePanel()">&times;</button>
  </div>
  <div class="panel-status-bar" id="panel-status-bar"></div>
  <div class="panel-body" id="panel-content"></div>
</div>

<script>
const PAGE_SIZE = 500;
let currentRunId = null;
let currentProductStatus = 'all';
let currentPage = 0;
let totalProducts = 0;
let autoRefreshInterval = null;

function badgeHtml(s) {
  const m = {success:'badge-success',failed:'badge-failed',pending:'badge-pending',running:'badge-running'};
  return `<span class="${m[s]||'badge-pending'}">${s}</span>`;
}
function tierHtml(t) { if(!t) return ''; return `<span class="tier-${t.replace('T','')}">${t}</span>`; }
async function fetchJson(url, opts={}) { const r = await fetch(url, opts); return r.json(); }

async function loadRuns() {
  const runs = await fetchJson('/api/runs');
  const sel = document.getElementById('run-select');
  sel.innerHTML = runs.map(r => `<option value="${r.id}">Run ${r.id} — ${r.status} (${r.total_products} products)</option>`).join('') || '<option>No runs</option>';
  if (runs.length && !currentRunId) { currentRunId = runs[0].id; sel.value = currentRunId; }
}

async function loadRunStats() {
  if (!currentRunId) return;
  try {
    const r = await fetchJson(`/api/runs/${currentRunId}`);
    document.getElementById('stat-progress').textContent = (r.progress_pct || 0) + '%';
    document.getElementById('progress-fill').style.width = (r.progress_pct || 0) + '%';
    document.getElementById('stat-counts').textContent = `${(r.enriched_count||0) + (r.failed_count||0)} / ${r.total_products||0}`;
    document.getElementById('stat-success').textContent = r.enriched_count || 0;
    document.getElementById('stat-failed').textContent = r.failed_count || 0;
    document.getElementById('stat-cost').textContent = '$' + (r.estimated_cost_usd || 0).toFixed(2);
    document.getElementById('stat-tokens').textContent = `${(r.total_input_tokens||0).toLocaleString()} in / ${(r.total_output_tokens||0).toLocaleString()} out`;
    document.getElementById('stat-writeback').textContent = r.writeback_status || 'pending';
    const badge = document.getElementById('run-status-badge');
    badge.textContent = r.status || 'unknown';
    badge.className = 'badge';
  } catch(e) {
    console.error('Failed to load run stats:', e);
  }
}

async function loadProducts() {
  if (!currentRunId) return;
  try {
    const statusParam = currentProductStatus === 'all' ? '' : `&status=${currentProductStatus}`;
    const offset = currentPage * PAGE_SIZE;
    const data = await fetchJson(`/api/runs/${currentRunId}/products?limit=${PAGE_SIZE}&offset=${offset}${statusParam}`);
    totalProducts = data.total || 0;
    const tbody = document.getElementById('products-tbody');
    if (!data.items || !data.items.length) {
      tbody.innerHTML = '<tr><td colspan="11" class="empty-state">No products</td></tr>';
      document.getElementById('pagination').innerHTML = '';
      return;
    }
    tbody.innerHTML = data.items.map(p => `
      <tr onclick="openPanel('${p.sku}')">
        <td style="font-family:monospace;font-size:12px;">${p.sku}</td>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${p.title||''}">${p.title||''}</td>
        <td>${tierHtml(p.tier)}</td><td>${badgeHtml(p.status)}</td>
        <td style="font-size:11px;color:#888;">${p.scrape_status||''}</td>
        <td>${badgeHtml(p.writeback_status)}</td>
        <td style="font-size:12px;color:#888;">${(p.input_tokens||0).toLocaleString()}</td>
        <td style="font-size:12px;color:#888;">${(p.output_tokens||0).toLocaleString()}</td>
        <td style="font-size:12px;color:#ccc;">$${(p.cost_usd||0).toFixed(4)}</td>
        <td style="font-size:12px;color:#888;">${p.retry_count||0}</td>
        <td onclick="event.stopPropagation()">
          <button class="action-btn scrape" onclick="rescrape('${p.sku}')">&#128269;</button>
          <button class="action-btn enrich" onclick="reenrich('${p.sku}')">&#128260;</button>
          <button class="action-btn writeback" onclick="writeback('${p.sku}')">&#128228;</button>
        </td>
      </tr>`).join('');
    renderPagination();
  } catch(e) {
    console.error('Failed to load products:', e);
    document.getElementById('products-tbody').innerHTML = '<tr><td colspan="11" class="empty-state">Error loading products – check console</td></tr>';
  }
}

function renderPagination() {
  const totalPages = Math.ceil(totalProducts / PAGE_SIZE);
  const pag = document.getElementById('pagination');
  if (totalPages <= 1) { pag.innerHTML = ''; return; }
  pag.innerHTML = `
    <button onclick="goToPage(${currentPage-1})" ${currentPage===0?'disabled':''}>&laquo; Prev</button>
    <span class="page-info">Page ${currentPage+1} of ${totalPages} (${totalProducts.toLocaleString()} products)</span>
    <button onclick="goToPage(${currentPage+1})" ${currentPage>=totalPages-1?'disabled':''}>Next &raquo;</button>
  `;
}

function goToPage(p) {
  if (p < 0 || p >= Math.ceil(totalProducts / PAGE_SIZE)) return;
  currentPage = p;
  loadProducts();
}

async function loadLogs() {
  if (!currentRunId) return;
  const data = await fetchJson(`/api/runs/${currentRunId}/logs?limit=200`);
  const c = document.getElementById('logs-container');
  c.innerHTML = data.length ? data.map(l => `
    <div class="log-entry">
      <span class="log-time">${l.timestamp.slice(0,19).replace('T',' ')}</span>
      <span class="log-level-${l.level}">${l.level}</span>
      <span style="color:#555;min-width:120px;">${l.module||''}</span>
      <span style="color:#888;min-width:100px;">${l.sku||''}</span>
      <span>${l.message}</span>
    </div>`).join('') : '<div class="empty-state">No logs</div>';
}

function switchTab(panel, status) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('products-panel').style.display = panel==='products'?'':'none';
  document.getElementById('logs-panel').style.display = panel==='logs'?'':'none';
  if (panel==='products') { currentProductStatus = status; currentPage = 0; loadProducts(); }
  else loadLogs();
}

function onRunChange() {
  currentRunId = parseInt(document.getElementById('run-select').value);
  currentPage = 0;
  loadAll();
}

// Side panel (unchanged)
async function openPanel(sku) {
  document.getElementById('overlay').classList.add('show');
  document.getElementById('side-panel').classList.add('open');
  document.getElementById('panel-title').textContent = sku + ' – Details';
  document.getElementById('panel-content').innerHTML = '<p style="color:#888;">Loading...</p>';
  const data = await fetchJson(`/api/products/${sku}/details`);
  if (data.error) { document.getElementById('panel-content').innerHTML = `<p style="color:#f87171;">${data.error}</p>`; return; }
  const shopify = data.shopify_data||{}, scraped = data.scraped_content||{}, enriched = data.enriched_data||{};
  document.getElementById('panel-status-bar').className = 'panel-status-bar '+(data.enrichment_status==='success'?'success':data.enrichment_status==='failed'?'failed':'pending');
  let enrichedHtml = '';
  if (Array.isArray(enriched.image_alt_texts)) {
    let list = '<ul class="alt-list">';
    enriched.image_alt_texts.forEach((a,i) => list += `<li><span class="alt-num">#${i+1}</span>${a}</li>`);
    list += '</ul>';
    const copy = {...enriched}; delete copy.image_alt_texts;
    enrichedHtml = `<strong style="color:#aaa;">Image Alt Texts</strong>${list}<pre>${JSON.stringify(copy,null,2)}</pre>`;
  } else enrichedHtml = `<pre>${JSON.stringify(enriched,null,2)}</pre>`;
  document.getElementById('panel-content').innerHTML = `
    <div style="margin-bottom:10px;"><span class="${data.enrichment_status==='success'?'badge-success':'badge-failed'}">${data.enrichment_status}</span> <span style="font-size:13px;color:#888;">Tier: ${data.tier||'?'} &middot; Cost: $${data.cost_usd.toFixed(4)}</span></div>
    <div class="accordion open"><div class="accordion-header" onclick="this.parentElement.classList.toggle('open')"><span>Shopify Data</span><span>&#9662;</span></div><div class="accordion-body">
      <div class="field-row"><span class="field-label">Title</span><span class="field-value">${shopify.title||''}</span></div>
      <div class="field-row"><span class="field-label">Vendor</span><span class="field-value">${shopify.vendor||''}</span></div>
      <pre>${JSON.stringify(shopify.existing_content||{},null,2)}</pre>
    </div></div>
    <div class="accordion"><div class="accordion-header" onclick="this.parentElement.classList.toggle('open')"><span>Scraped Content</span><span>&#9662;</span></div><div class="accordion-body">
      <pre>${JSON.stringify(scraped,null,2)}</pre>
    </div></div>
    <div class="accordion"><div class="accordion-header" onclick="this.parentElement.classList.toggle('open')"><span>Enriched Content</span><span>&#9662;</span></div><div class="accordion-body">${enrichedHtml}</div></div>
    <div class="accordion"><div class="accordion-header" onclick="this.parentElement.classList.toggle('open')"><span>Writeback</span><span>&#9662;</span></div><div class="accordion-body">
      <div class="field-row"><span class="field-label">Status</span><span class="field-value">${data.writeback.status||'pending'}</span></div>
      ${data.writeback.error?`<div class="field-row"><span class="field-label">Error</span><span class="field-value" style="color:#f87171;">${data.writeback.error}</span></div>`:''}
    </div></div>`;
}
function closePanel() { document.getElementById('overlay').classList.remove('show'); document.getElementById('side-panel').classList.remove('open'); }

// Per-product actions
async function rescrape(sku) {
  const u = prompt('Custom URL (blank for auto-search):','');
  const body = u ? JSON.stringify({custom_url:u}) : '{}';
  const d = await fetchJson(`/api/products/${sku}/scrape`, {method:'POST', headers:{'Content-Type':'application/json'}, body});
  alert(`Scrape: ${d.scrape_status||d.error}`);
  loadProducts();
}
async function reenrich(sku) {
  const d = await fetchJson(`/api/products/${sku}/enrich`, {method:'POST'});
  alert(`Enrichment: ${d.status}\n${d.error||''}`);
  loadProducts();
}
async function writeback(sku) {
  if (!confirm(`Write back ${sku}?`)) return;
  const d = await fetchJson(`/api/products/${sku}/writeback`, {method:'POST'});
  alert(d.status==='success'?`Writeback: success\n${d.passes_completed.join(', ')}`:`Writeback: failed\n${(d.errors||[]).join('\n')}`);
  loadProducts();
}

async function loadAll() { await loadRuns(); await loadRunStats(); await loadProducts(); }
loadAll();
autoRefreshInterval = setInterval(loadAll, 5000);
</script>
</body>
</html>
"""
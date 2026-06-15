"""
dashboard.py -- FastAPI web dashboard for monitoring pipeline runs.

Run alongside the pipeline:
    uvicorn dashboard:app --host 0.0.0.0 --port 8080 --reload

Access at: http://localhost:8080
"""

from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from database import get_db, Run, Enrichment, Product, Log, get_run_stats
from config import config

app = FastAPI(title="Mega Enrichment Dashboard", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_manual_run(db) -> int:
    """Return the run_id for manual per-product actions. Creates one if needed."""
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
        runs = db.query(Run).order_by(Run.started_at.desc()).limit(20).all()
        return [get_run_stats(db, r.id) for r in runs]


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    with get_db() as db:
        return get_run_stats(db, run_id)


@app.get("/api/runs/{run_id}/products")
def get_run_products(run_id: int, status: Optional[str] = None, limit: int = 100, offset: int = 0):
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

        return {"total": total, "items": rows}


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


# ── Per-product action endpoints ──────────────────────────────────────────────

@app.post("/api/products/{sku}/scrape")
def rescrape_product(sku: str, custom_url: Optional[str] = None):
    """Re-scrape a single product. Optionally use a provided URL."""
    from scraper import scrape_product, scrape_url

    with get_db() as db:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            return JSONResponse({"error": "SKU not found"}, status_code=404)

        manual_run_id = _get_or_create_manual_run(db)

        # Perform scrape
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

        # Find the latest enrichment for this SKU, or create one
        enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
        if not enrichment:
            enrichment = Enrichment(
                run_id=manual_run_id,
                product_id=product.id,
                sku=sku,
                status="pending",
            )
            db.add(enrichment)
            db.flush()

        enrichment.scrape_status = supplier_content.get("status", "manual")
        enrichment.scraped_content = supplier_content
        db.commit()

        return {
            "status": "ok",
            "sku": sku,
            "scrape_status": enrichment.scrape_status,
        }


@app.post("/api/products/{sku}/enrich")
def reenrich_product(sku: str):
    """Re-enrich a single product using Claude."""
    import anthropic
    import httpx
    from claude_enricher import (
        classify_tier, _load_prompt, _build_user_message,
        _max_tokens, _cost_usd,
    )
    from validator import validate_claude_response
    from scraper import scrape_product

    with get_db() as db:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            return JSONResponse({"error": "SKU not found"}, status_code=404)

        manual_run_id = _get_or_create_manual_run(db)

        # Find or create enrichment row
        enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
        if not enrichment:
            enrichment = Enrichment(
                run_id=manual_run_id,
                product_id=product.id,
                sku=sku,
                status="pending",
            )
            db.add(enrichment)
            db.flush()

        # Get supplier content (use existing scraped content or scrape now)
        supplier_content = enrichment.scraped_content or {}
        if not supplier_content or not supplier_content.get("status"):
            mpn = getattr(product, "mpn", "") or ""
            supplier_content = scrape_product(sku, product.vendor or "", product.title or "", mpn=mpn)
            enrichment.scrape_status = supplier_content.get("status", "manual")
            enrichment.scraped_content = supplier_content
            db.flush()

        # Determine tier
        has_feed_desc = bool(
            supplier_content.get("description", "").strip()
            or supplier_content.get("features", "").strip()
        )
        has_brand_url = supplier_content.get("status") in ("success", "cached", "csv_export")
        image_count = len(product.images or [])
        tier = classify_tier(
            has_feed_desc, has_brand_url, image_count,
            existing_content=product.existing_content or {},
        )

        # Build the product data dict for Claude
        product_data = {
            "title": product.title or "",
            "brand": product.vendor or "",
            "vendor": product.vendor or "",
            "sku": product.sku,
            "price": product.price or "",
            "barcode": getattr(product, "barcode", "") or "",
            "existing_content": product.existing_content or {},
            "category_path": "Office Supplies",
            "compatible_with": "Not specified.",
            "rrp": getattr(product, "rrp", "") or "",
            "gtin": getattr(product, "barcode", "") or "",
        }

        # Call Claude (synchronous)
        client = anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        system_prompt = _load_prompt("system.txt")
        user_message = _build_user_message(product_data, supplier_content, tier)

        try:
            response = client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=_max_tokens(tier),
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

            is_valid, parsed, error = validate_claude_response(raw, tier)
            if is_valid:
                enrichment.status = "success"
                enrichment.tier = tier
                enrichment.enriched_data = parsed
                enrichment.claude_input_tokens = input_tokens
                enrichment.claude_output_tokens = output_tokens
                enrichment.cost_usd = _cost_usd(input_tokens, output_tokens)
                enrichment.error_message = ""
                enrichment.retry_count = 0
            else:
                enrichment.status = "failed"
                enrichment.tier = tier
                enrichment.error_message = error or "Validation failed"
                enrichment.retry_count = 1

            db.commit()
            return {
                "status": enrichment.status,
                "sku": sku,
                "tier": tier,
                "cost_usd": round(enrichment.cost_usd or 0, 5),
                "input_tokens": enrichment.claude_input_tokens,
                "output_tokens": enrichment.claude_output_tokens,
                "error": enrichment.error_message or "",
            }

        except Exception as e:
            db.rollback()                                          # ← reset the failed session
            enrichment.status = "failed"
            enrichment.error_message = str(e)[:200]
            enrichment.retry_count = 3
            db.commit()
            return JSONResponse(
                {"status": "failed", "sku": sku, "error": str(e)[:200]},
                status_code=500,
            )


@app.post("/api/products/{sku}/writeback")
def writeback_product(sku: str):
    import requests as _requests
    from token_manager import get_headers
    from shopify_bulk import (
        PRODUCT_UPDATE_MUTATION, METAFIELDS_SET_MUTATION,
    )
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
                return [f"{key} returned null – possible field error"]
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
            return JSONResponse(
                {"error": "No successful enrichment found for this SKU"},
                status_code=400,
            )

        enriched = enrichment.enriched_data or {}
        headers = get_headers()
        errors = []
        passes_ok = []

        # --- Pass A: productUpdate ---
        product_variables = {
            "product": {
                "id": product.shopify_product_id,
                "title": enriched.get("title", product.title or ""),
                "descriptionHtml": enriched.get("body_html", ""),
                "vendor": product.vendor or "",
                "tags": enriched.get("tags", []),
                "seo": {
                    "title": enriched.get("seo_title", ""),
                    "description": enriched.get("seo_description", ""),
                },
            }
        }
        try:
            resp = _requests.post(
                config.shopify_graphql_url,
                headers=headers,
                json={"query": PRODUCT_UPDATE_MUTATION, "variables": product_variables},
                timeout=30,
            )
            errs = _safe_errors(resp)
            if any("returned null" in e for e in errs):
                errors.extend(errs)
            elif errs:
                errors.extend([f"productUpdate: {e['message']}" for e in errs])
            else:
                passes_ok.append("productUpdate")
        except Exception as e:
            errors.append(f"productUpdate exception: {str(e)[:120]}")

        # --- Pass B: metafieldsSet ---
        metafields = prepare_metafields(enriched)
        if metafields:
            for mf in metafields:
                mf["ownerId"] = product.shopify_product_id
            try:
                resp = _requests.post(
                    config.shopify_graphql_url,
                    headers=headers,
                    json={"query": METAFIELDS_SET_MUTATION, "variables": {"metafields": metafields}},
                    timeout=30,
                )
                errs = _safe_errors(resp)
                if any("returned null" in e for e in errs):
                    errors.extend(errs)
                elif errs:
                    errors.extend([f"metafieldsSet: {e['message']}" for e in errs])
                else:
                    passes_ok.append("metafieldsSet")
            except Exception as e:
                errors.append(f"metafieldsSet exception: {str(e)[:120]}")

        # --- Pass C: fileUpdate – skipped (needs MediaImage IDs, not ProductImage IDs) ---
        passes_ok.append("fileUpdate (skipped)")

        if errors:
            enrichment.writeback_status = "failed"
            enrichment.writeback_error = "; ".join(errors)
            db.commit()
            return JSONResponse(
                {"status": "failed", "sku": sku, "errors": errors},
                status_code=500,
            )

        enrichment.writeback_status = "success"
        enrichment.writeback_error = ""
        db.commit()

        return {
            "status": "success",
            "sku": sku,
            "passes_completed": passes_ok,
        }

# ── HTML dashboard ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mega Office Enrichment Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; }
  .header { background: #1a1d2e; border-bottom: 1px solid #2d3748; padding: 16px 24px;
            display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 18px; font-weight: 600; color: #fff; }
  .header .badge { background: #2d6a4f; color: #74c69d; padding: 3px 10px;
                   border-radius: 999px; font-size: 12px; font-weight: 500; }
  .main { padding: 24px; max-width: 1500px; margin: 0 auto; }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 16px; margin-bottom: 24px; }
  .stat-card { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 10px; padding: 20px; }
  .stat-card .label { font-size: 12px; color: #718096; text-transform: uppercase;
                      letter-spacing: 0.05em; margin-bottom: 8px; }
  .stat-card .value { font-size: 28px; font-weight: 700; color: #fff; }
  .stat-card .sub { font-size: 13px; color: #718096; margin-top: 4px; }
  .progress-bar { background: #2d3748; border-radius: 999px; height: 8px;
                  margin: 12px 0; overflow: hidden; }
  .progress-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #667eea, #764ba2);
                   transition: width 0.5s ease; }
  .section { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 10px;
             padding: 20px; margin-bottom: 20px; }
  .section h2 { font-size: 15px; font-weight: 600; color: #fff; margin-bottom: 16px;
                display: flex; align-items: center; gap: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 8px; border-bottom: 1px solid #2d3748;
       color: #718096; font-weight: 500; text-transform: uppercase; font-size: 11px;
       letter-spacing: 0.05em; }
  td { padding: 8px; border-bottom: 1px solid #1e2330; color: #cbd5e0; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1e2330; }
  .badge-success { background: #1a3a2a; color: #74c69d; padding: 2px 8px;
                   border-radius: 4px; font-size: 11px; font-weight: 500; }
  .badge-failed  { background: #3a1a1a; color: #fc8181; padding: 2px 8px;
                   border-radius: 4px; font-size: 11px; font-weight: 500; }
  .badge-pending { background: #2a2a1a; color: #f6e05e; padding: 2px 8px;
                   border-radius: 4px; font-size: 11px; font-weight: 500; }
  .badge-running { background: #1a2a3a; color: #63b3ed; padding: 2px 8px;
                   border-radius: 4px; font-size: 11px; font-weight: 500; }
  .tier-1 { color: #e6a817; font-weight: 600; }
  .tier-2 { color: #667eea; font-weight: 600; }
  .tier-3 { color: #718096; font-weight: 600; }
  .log-entry { font-family: monospace; font-size: 12px; padding: 6px 10px;
               border-bottom: 1px solid #1e2330; display: flex; gap: 12px; }
  .log-time { color: #4a5568; min-width: 160px; }
  .log-level-INFO { color: #63b3ed; min-width: 60px; }
  .log-level-ERROR { color: #fc8181; min-width: 60px; }
  .log-level-WARNING { color: #f6e05e; min-width: 60px; }
  .tabs { display: flex; gap: 4px; margin-bottom: 16px; }
  .tab { padding: 7px 16px; border-radius: 6px; font-size: 13px; cursor: pointer;
         border: 1px solid #2d3748; background: none; color: #718096;
         transition: all 0.15s; }
  .tab.active { background: #667eea; border-color: #667eea; color: #fff; font-weight: 500; }
  .tab:hover:not(.active) { color: #e2e8f0; background: #1e2330; }
  .run-selector { margin-bottom: 20px; display: flex; align-items: center; gap: 12px; }
  select { background: #1a1d2e; border: 1px solid #2d3748; color: #e2e8f0;
           padding: 7px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; }
  .refresh-btn { background: #2d3748; border: none; color: #e2e8f0; padding: 7px 14px;
                 border-radius: 6px; cursor: pointer; font-size: 13px; }
  .refresh-btn:hover { background: #4a5568; }
  #auto-refresh { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #718096; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #74c69d;
         animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .cost-highlight { color: #74c69d; font-weight: 600; }
  .empty-state { text-align: center; padding: 40px; color: #4a5568; font-size: 14px; }
  .action-btn { background: #2d3748; border: 1px solid #4a5568; color: #cbd5e0;
                padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 13px;
                margin-right: 2px; transition: all 0.15s; }
  .action-btn:hover { background: #4a5568; color: #fff; border-color: #718096; }
  .action-btn.scrape { color: #63b3ed; }
  .action-btn.enrich { color: #f6e05e; }
  .action-btn.writeback { color: #74c69d; }
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

<div class="main">
  <div class="run-selector">
    <label style="font-size:13px;color:#718096;">Run:</label>
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
      <div class="value" id="stat-success" style="color:#74c69d">0</div>
    </div>
    <div class="stat-card">
      <div class="label">Failed</div>
      <div class="value" id="stat-failed" style="color:#fc8181">0</div>
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
      <table>
        <thead>
          <tr>
            <th>SKU</th><th>Title</th><th>Tier</th><th>Status</th>
            <th>Scrape</th><th>Write-back</th><th>Tokens in</th>
            <th>Tokens out</th><th>Cost</th><th>Retries</th><th>Actions</th>
          </tr>
        </thead>
        <tbody id="products-tbody">
          <tr><td colspan="11" class="empty-state">Select a run to view products</td></tr>
        </tbody>
      </table>
    </div>

    <div id="logs-panel" style="display:none;">
      <div id="logs-container"></div>
    </div>
  </div>
</div>

<script>
let currentRunId = null;
let currentProductStatus = 'all';
let autoRefreshInterval = null;

function badgeHtml(status) {
  const cls = {success:'badge-success', failed:'badge-failed',
               pending:'badge-pending', running:'badge-running'}[status] || 'badge-pending';
  return `<span class="${cls}">${status}</span>`;
}

function tierHtml(tier) {
  if (!tier) return '';
  const t = tier.replace('T','');
  return `<span class="tier-${t}">${tier}</span>`;
}

async function fetchJson(url, options = {}) {
  const r = await fetch(url, options);
  return r.json();
}

async function loadRuns() {
  const runs = await fetchJson('/api/runs');
  const sel = document.getElementById('run-select');
  sel.innerHTML = runs.length
    ? runs.map(r => `<option value="${r.id}">${r.id} — ${r.status} (${r.total_products} products) ${r.started_at ? r.started_at.slice(0,16) : ''}</option>`).join('')
    : '<option>No runs yet</option>';
  if (runs.length && !currentRunId) {
    currentRunId = runs[0].id;
    sel.value = currentRunId;
  }
}

async function loadRunStats() {
  if (!currentRunId) return;
  const r = await fetchJson(`/api/runs/${currentRunId}`);
  document.getElementById('stat-progress').textContent = r.progress_pct + '%';
  document.getElementById('progress-fill').style.width = r.progress_pct + '%';
  document.getElementById('stat-counts').textContent =
    `${r.enriched_count + r.failed_count} / ${r.total_products} products`;
  document.getElementById('stat-success').textContent = r.enriched_count;
  document.getElementById('stat-failed').textContent = r.failed_count;
  document.getElementById('stat-cost').textContent = '$' + r.estimated_cost_usd.toFixed(4);
  document.getElementById('stat-tokens').textContent =
    `${(r.total_input_tokens||0).toLocaleString()} in / ${(r.total_output_tokens||0).toLocaleString()} out`;
  document.getElementById('stat-writeback').textContent = r.writeback_status;
  const badge = document.getElementById('run-status-badge');
  badge.textContent = r.status;
  badge.className = 'badge';
  if (r.status === 'running') { badge.style.background = '#1a2a3a'; badge.style.color = '#63b3ed'; }
  else if (r.status === 'completed') { badge.style.background = '#1a3a2a'; badge.style.color = '#74c69d'; }
  else if (r.status === 'failed') { badge.style.background = '#3a1a1a'; badge.style.color = '#fc8181'; }
}

async function loadProducts(status = 'all') {
  if (!currentRunId) return;
  const url = status === 'all'
    ? `/api/runs/${currentRunId}/products?limit=200`
    : `/api/runs/${currentRunId}/products?status=${status}&limit=200`;
  const data = await fetchJson(url);
  const tbody = document.getElementById('products-tbody');
  if (!data.items.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty-state">No products found</td></tr>';
    return;
  }
  tbody.innerHTML = data.items.map(p => `
    <tr>
      <td style="font-family:monospace;font-size:12px;">${p.sku}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
          title="${p.title}">${p.title}</td>
      <td>${tierHtml(p.tier)}</td>
      <td>${badgeHtml(p.status)}</td>
      <td style="font-size:11px;color:#718096;">${p.scrape_status || ''}</td>
      <td>${badgeHtml(p.writeback_status)}</td>
      <td style="font-size:12px;color:#718096;">${(p.input_tokens||0).toLocaleString()}</td>
      <td style="font-size:12px;color:#718096;">${(p.output_tokens||0).toLocaleString()}</td>
      <td style="font-size:12px;" class="cost-highlight">$${(p.cost_usd||0).toFixed(5)}</td>
      <td style="font-size:12px;color:#718096;">${p.retry_count}</td>
      <td>
        <button class="action-btn scrape" onclick="rescrape('${p.sku}')" title="Re-scrape">&#128269;</button>
        <button class="action-btn enrich" onclick="reenrich('${p.sku}')" title="Re-enrich">&#128260;</button>
        <button class="action-btn writeback" onclick="writeback('${p.sku}')" title="Write-back">&#128228;</button>
      </td>
    </tr>`).join('');
}

async function loadLogs() {
  if (!currentRunId) return;
  const data = await fetchJson(`/api/runs/${currentRunId}/logs?limit=200`);
  const container = document.getElementById('logs-container');
  if (!data.length) {
    container.innerHTML = '<div class="empty-state">No logs yet</div>';
    return;
  }
  container.innerHTML = data.map(l => `
    <div class="log-entry">
      <span class="log-time">${l.timestamp.slice(0,19).replace('T',' ')}</span>
      <span class="log-level-${l.level}">${l.level}</span>
      <span style="color:#4a5568;min-width:120px;">${l.module || ''}</span>
      <span style="color:#718096;min-width:100px;">${l.sku || ''}</span>
      <span>${l.message}</span>
    </div>`).join('');
}

function switchTab(panel, status) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('products-panel').style.display = panel === 'products' ? '' : 'none';
  document.getElementById('logs-panel').style.display = panel === 'logs' ? '' : 'none';
  if (panel === 'products') { currentProductStatus = status; loadProducts(status); }
  else { loadLogs(); }
}

function onRunChange() {
  currentRunId = parseInt(document.getElementById('run-select').value);
  loadAll();
}

// ── Per-product actions ──────────────────────────────────────────────────

async function rescrape(sku) {
  const customUrl = prompt('Enter custom URL (or leave blank for auto-search):', '');
  const body = customUrl ? JSON.stringify({custom_url: customUrl}) : undefined;
  try {
    const data = await fetchJson(`/api/products/${sku}/scrape`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: body || '{}',
    });
    alert(`Scrape: ${data.scrape_status || data.error}`);
  } catch (e) {
    alert('Scrape failed: ' + e.message);
  }
  loadAll();
}

async function reenrich(sku) {
  try {
    const data = await fetchJson(`/api/products/${sku}/enrich`, {method: 'POST'});
    if (data.status === 'success') {
      alert(`Enrichment: ${data.status}\nTier: ${data.tier}\nCost: $${(data.cost_usd||0).toFixed(4)}\nTokens: ${data.input_tokens} in / ${data.output_tokens} out`);
    } else {
      alert(`Enrichment: ${data.status}\nError: ${data.error || 'Unknown'}`);
    }
  } catch (e) {
    alert('Enrichment failed: ' + e.message);
  }
  loadAll();
}

async function writeback(sku) {
  if (!confirm(`Write back ${sku} to Shopify? This will update the live product.`)) return;
  try {
    const data = await fetchJson(`/api/products/${sku}/writeback`, {method: 'POST'});
    if (data.status === 'success') {
      alert(`Writeback: success\nPasses: ${data.passes_completed.join(', ')}`);
    } else {
      alert(`Writeback: failed\n${(data.errors||[]).join('\\n')}`);
    }
  } catch (e) {
    alert('Writeback failed: ' + e.message);
  }
  loadAll();
}

// ── Initial load ─────────────────────────────────────────────────────────

async function loadAll() {
  await loadRuns();
  await loadRunStats();
  await loadProducts(currentProductStatus);
}

loadAll();
autoRefreshInterval = setInterval(loadAll, 5000);
</script>
</body>
</html>
"""
# Mega Office Supplies -- Product Enrichment Pipeline v2

AI-powered bulk enrichment. Shopify Plus Bulk Operations API + Claude + PostgreSQL + Web Dashboard.

---

## What is new in v2

- Auto-minting Shopify token (client credentials grant, 24h expiry, auto-refreshes mid-run)
- PostgreSQL backend: all products, enrichments, logs, and run history in one DB
- Matrixify CSV loader: parses the Shopify export directly into the DB
- Web dashboard at http://localhost:8080 (run status, progress, per-product table, logs, cost)
- Resume any interrupted run with --resume <run_id>

---

## Prerequisites

- Python 3.10+
- PostgreSQL 14+ (local or hosted -- client provides their own instance)
- Shopify Plus store with a custom app (Client ID + Client Secret)
- Anthropic API key

---

## Setup

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
cp .env.example .env
# Edit .env:
#   SHOPIFY_STORE_URL=mega-office-supplies.myshopify.com
#   SHOPIFY_CLIENT_ID=<from 1Password link>
#   SHOPIFY_CLIENT_SECRET=<from 1Password link>
#   ANTHROPIC_API_KEY=<from 1Password link>
#   DATABASE_URL=postgresql://user:password@localhost:5432/mega_enrichment

# 4. Create the database (PostgreSQL must be running)
createdb mega_enrichment
python -c "from database import init_db; init_db()"

# 5. Add client prompt templates (provided on day one)
#    prompts/tier1.txt
#    prompts/tier2.txt
#    prompts/tier3.txt
```

---

## How to Use This Application

### Step 1 -- Load the product CSV

You have received a Matrixify export from Shopify. Load it into the database first:

```bash
# Preview what is in the CSV without loading
python csv_loader.py --file products_export.csv --summary

# Load everything
python csv_loader.py --file products_export.csv

# Load only the first 200 products (for the test run)
python csv_loader.py --file products_export.csv --limit 200
```

This reads the CSV, collapses multi-row variants into one product per SKU,
determines enrichment tier per product, and upserts everything into the products table.

### Step 2 -- Start the dashboard (optional but recommended)

Open a second terminal:

```bash
uvicorn dashboard:app --host 0.0.0.0 --port 8080
```

Then open http://localhost:8080 in your browser. It auto-refreshes every 5 seconds
and shows live progress, per-product status, token usage, and running cost.

### Step 3 -- Run Milestone 1: 100-product test

```bash
python pipeline.py --limit 100 --skip-writeback
```

This:
1. Mints a Shopify token and runs a smoke test
2. Fetches live Shopify data for all products (bulk query)
3. Scrapes supplier pages for specs and features
4. Sends each product to Claude and validates the response
5. Saves all enriched data to the DB
6. Skips write-back (you review first)

Review the dashboard and the output/run_log table, then write back:

```bash
python pipeline.py --writeback-only --resume <run_id>
```

The run_id is printed at startup and visible in the dashboard run selector.

### Step 4 -- Run Milestone 2: 500-product QA

```bash
python csv_loader.py --file products_export.csv --limit 500
python pipeline.py --limit 500
```

### Step 5 -- Full 28,000-product run

```bash
# Load full CSV
python csv_loader.py --file products_export.csv

# Run everything (split into enrich + writeback for safety)
python pipeline.py --skip-writeback

# Review dashboard, then write back
python pipeline.py --writeback-only --resume <run_id>
```

### Resuming an interrupted run

If the pipeline crashes at any point:

```bash
# Check which run IDs exist
python -c "from database import get_db, Run; db=next(iter([get_db().__enter__()])); [print(r.id, r.status, r.enriched_count) for r in db.query(Run).all()]"

# Resume
python pipeline.py --resume <run_id>
```

Already-enriched products are skipped automatically -- only pending ones are processed.

---

## Pipeline Flags

| Flag | Description |
|---|---|
| --limit N | Process only first N products |
| --skip-fetch | Skip Shopify bulk query (use existing DB data) |
| --skip-writeback | Enrich only, do not write to Shopify |
| --writeback-only | Write back from DB without re-enriching (requires --resume) |
| --resume RUN_ID | Resume or write back a specific run |

---

## Database Tables

| Table | Contents |
|---|---|
| products | All products loaded from CSV + Shopify data merged in |
| enrichments | One row per product per run: Claude output, tokens, cost, status |
| runs | One row per pipeline execution: totals, status, cost |
| logs | Every pipeline event, error, and retry |
| shopify_tokens | Token history with expiry timestamps |

Connect any Postgres client (TablePlus, DBeaver, pgAdmin) to inspect the data directly.

---

## Token Auto-refresh

The pipeline uses Shopify's client credentials grant. The token expires every 24 hours.
The pipeline handles this automatically:

- On startup: mints a fresh token (or loads a valid one from DB)
- During the run: a background thread checks every 10 minutes and refreshes 5 minutes before expiry
- All Shopify API calls always use the current live token via get_headers()

No manual token management is needed.

---

## Supplier Scraping

The scraper hits supplier pages to pull product specs and features.
Results are cached in the DB (scrape_status column on enrichments).

If a supplier site blocks requests, the pipeline:
1. Logs the block (blocked_403, blocked_robots, timeout, etc.)
2. Falls back to enriching from Shopify data alone
3. Flags the product for potential manual review

The scraper respects robots.txt and uses a 2-second delay between requests.

---

## Architecture

```
pipeline.py        Orchestrator: runs phases, manages run lifecycle, resume logic
token_manager.py   Shopify client credentials token: mint, cache, auto-refresh
csv_loader.py      Matrixify CSV parser: upserts products into DB
shopify_fetch.py   bulkOperationRunQuery: fetches all Shopify product data
claude_enricher.py 20 concurrent Claude calls: async, validated, DB write-back
scraper.py         Supplier page scraper: cached, robots.txt, graceful fallback
shopify_bulk.py    bulkOperationRunMutation: JSONL build, stage, submit, poll
database.py        SQLAlchemy models: Run, Product, Enrichment, Log, ShopifyToken
validator.py       JSON structure validation, SEO length checks, metafield format
dashboard.py       FastAPI: /api/runs, /api/runs/{id}/products, /api/runs/{id}/logs
config.py          All settings via .env -- no hardcoded credentials
prompts/           Tier prompt templates (provided by client on day one)
```

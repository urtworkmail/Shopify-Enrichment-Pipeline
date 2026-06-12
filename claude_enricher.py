"""
claude_enricher.py -- Claude API enrichment with DB-backed state and correct tier logic.

Tier classification per handoff doc (data-driven, NOT price-based):
    T1: has_feed_description AND has_brand_url AND image_count >= 3
    T2: has_feed_description OR (has_brand_url AND image_count >= 1)
    T3: everything else
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic

from config import config
from database import get_db, Product, Enrichment, Run
from validator import validate_claude_response

PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    path = PROMPT_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found: {path}")


def existing_content_is_substantial(existing: dict) -> bool:
    """
    Addendum 3.1 Section B: existing content counts as a first-class data source.
    Substantial = key_features OR specifications has real content beyond trivial junk.
    """
    for key in ("custom.key_features", "custom.specifications"):
        val = str(existing.get(key, "")).strip()
        if len(val) > 80 and val.lower() not in ("nothing: here", "n/a", "none"):
            return True
    return False


def classify_tier(
    has_feed_description: bool,
    has_brand_url: bool,
    image_count: int,
    existing_content: dict = None,
) -> str:
    """
    Classify enrichment tier per addendum 3.1 Section B.
    Existing substantial content lifts a product to T2 -- not T3.
    T3 is reserved for products with NO supplier data, NO brand access,
    AND little/no usable existing content.
    """
    has_existing = existing_content_is_substantial(existing_content or {})

    if has_feed_description and has_brand_url and image_count >= 3:
        return "T1"
    elif has_feed_description or (has_brand_url and image_count >= 1) or has_existing:
        return "T2"
    else:
        return "T3"


def _max_tokens(tier: str) -> int:
    return {"T1": config.TIER1_MAX_TOKENS,
            "T2": config.TIER2_MAX_TOKENS,
            "T3": config.TIER3_MAX_TOKENS}.get(tier, 600)


def _build_user_message(product_data: dict, supplier_content: dict, tier: str) -> str:
    """Build the user-turn message to send to Claude."""
    has_supplier = supplier_content.get("status") in ("success", "cached", "csv_export")

    feed_description = ""
    feed_specs = ""
    if has_supplier:
        feed_description = supplier_content.get("description", "")[:2000]
        feed_specs = supplier_content.get("specifications", "")[:800]

    # Addendum 3.1 Section E: extract each existing field individually
    # Priority: supplier/brand scrape > existing store content > inference
    existing = product_data.get("existing_content") or {}

    existing_body_html     = existing.get("custom.body_html", "") or existing.get("description_html", "") or "None"
    existing_key_features  = existing.get("custom.key_features", "") or "None"
    existing_applications  = existing.get("custom.applications", "") or "None"
    existing_specifications = existing.get("custom.specifications", "") or "None"
    existing_pack_size     = existing.get("custom.pack_size", "") or "None"
    existing_unit          = existing.get("custom.unit", "") or "None"

    if tier == "T3":
        template = _load_prompt("tier3.txt")
    else:
        template = _load_prompt("tier1_tier2.txt")

    return template.format(
        raw_title=product_data.get("title", ""),
        brand=product_data.get("brand", product_data.get("vendor", "")),
        vendor_sku=product_data.get("sku", ""),
        category_path=product_data.get("category_path", "Office Supplies"),
        feed_description=feed_description or "Not available.",
        feed_specs=feed_specs or "Not available.",
        compatible_with=product_data.get("compatible_with", "Not specified."),
        price=product_data.get("price", ""),
        rrp=product_data.get("rrp", ""),
        gtin=product_data.get("gtin", product_data.get("barcode", "")),
        tier=tier,
        existing_body_html=existing_body_html[:1500],
        existing_key_features=existing_key_features[:600],
        existing_applications=existing_applications[:500],
        existing_specifications=existing_specifications[:600],
        existing_pack_size=existing_pack_size[:50],
        existing_unit=existing_unit[:30],
    )


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * config.PRICE_INPUT_PER_MTK
        + output_tokens / 1_000_000 * config.PRICE_OUTPUT_PER_MTK
    )


async def _enrich_one(
    client: anthropic.AsyncAnthropic,
    semaphore: asyncio.Semaphore,
    enrichment_id: int,
    product_data: dict,
    supplier_content: dict,
    tier: str,
) -> dict:
    """Enrich a single product with retry logic. Returns result dict."""
    sku = product_data.get("sku", "unknown")
    system_prompt = _load_prompt("system.txt")

    async with semaphore:
        for attempt in range(1, config.CLAUDE_MAX_RETRIES + 1):
            try:
                user_message = _build_user_message(product_data, supplier_content, tier)
                print(f"[claude] {sku}: calling (attempt {attempt})")

                response = await client.messages.create(
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
                    return {
                        "enrichment_id": enrichment_id,
                        "sku": sku,
                        "tier": tier,
                        "status": "success",
                        "enriched_data": parsed,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": _cost_usd(input_tokens, output_tokens),
                        "retry_count": attempt - 1,
                        "error": "",
                    }

                print(f"[claude] {sku} attempt {attempt} validation failed: {error}")

            except anthropic.RateLimitError:
                wait = 2 ** attempt * 5
                print(f"[claude] Rate limit on {sku} -- waiting {wait}s")
                await asyncio.sleep(wait)
            except anthropic.APIError as e:
                wait = 2 ** attempt
                print(f"[claude] API error on {sku} attempt {attempt}: {e} -- waiting {wait}s")
                await asyncio.sleep(wait)
            except Exception as e:
                return {
                    "enrichment_id": enrichment_id,
                    "sku": sku,
                    "tier": tier,
                    "status": "failed",
                    "enriched_data": None,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "retry_count": attempt - 1,
                    "error": str(e),
                }

    return {
        "enrichment_id": enrichment_id,
        "sku": sku,
        "tier": tier,
        "status": "failed",
        "enriched_data": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "retry_count": config.CLAUDE_MAX_RETRIES,
        "error": "Max retries exceeded",
    }


async def _run_batch_async(items: list[tuple]) -> list[dict]:
    """items: list of (enrichment_id, product_data, supplier_content, tier)"""
    import httpx
    client = anthropic.AsyncAnthropic(
        api_key=config.ANTHROPIC_API_KEY,
        timeout=httpx.Timeout(60.0, connect=10.0)
    )
    semaphore = asyncio.Semaphore(config.CONCURRENT_CLAUDE_CALLS)

    tasks = [
        _enrich_one(client, semaphore, eid, pdata, scontent, tier)
        for eid, pdata, scontent, tier in items
    ]

    results = []
    total = len(tasks)
    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        result = await coro
        results.append(result)
        if i % 10 == 0 or i == total:
            print(f"[claude] {i}/{total} enriched")
    return results


def write_results_to_db(results: list[dict], run_id: int):
    """Persist enrichment results to DB and update run totals."""
    with get_db() as db:
        run = db.query(Run).filter_by(id=run_id).first()
        for r in results:
            enrichment = db.query(Enrichment).filter_by(id=r["enrichment_id"]).first()
            if not enrichment:
                continue
            enrichment.status = r["status"]
            enrichment.tier = r.get("tier")
            enrichment.enriched_data = r["enriched_data"]
            enrichment.claude_input_tokens = r["input_tokens"]
            enrichment.claude_output_tokens = r["output_tokens"]
            enrichment.cost_usd = r["cost_usd"]
            enrichment.retry_count = r["retry_count"]
            enrichment.error_message = r["error"]
            enrichment.needs_manual_review = r["status"] == "failed"
            enrichment.updated_at = datetime.utcnow()

            if run:
                if r["status"] == "success":
                    run.enriched_count = (run.enriched_count or 0) + 1
                else:
                    run.failed_count = (run.failed_count or 0) + 1
                run.total_input_tokens = (run.total_input_tokens or 0) + r["input_tokens"]
                run.total_output_tokens = (run.total_output_tokens or 0) + r["output_tokens"]
                run.estimated_cost_usd = (run.estimated_cost_usd or 0.0) + r["cost_usd"]


def enrich_batch(items: list[tuple], run_id: int) -> list[dict]:
    """
    Synchronous entry point.
    items: list of (enrichment_id, product_data_dict, supplier_content_dict, tier_str)
    """
    results = asyncio.run(_run_batch_async(items))
    write_results_to_db(results, run_id)
    return results

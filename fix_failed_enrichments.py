"""
fix_failed_enrichments.py – automatically re‑enrich genuine failures
(JSON parse errors and Batch API unknowns). Handles DB connection drops.
"""

import anthropic
import httpx
from config import config
from database import SessionLocal, Product, Enrichment, Run
from claude_enricher import (
    classify_tier, _load_prompt, _build_user_message,
    _max_tokens, _cost_usd,
)
from validator import validate_claude_response
from scraper import scrape_product

FIX_SKUS = [
    # JSON parse errors (8)
    "CD365.V49-27",
    "CSPRZ104",
    "DS407825",
    "EVLSCN41000",
    "EVLWCLPC",
    "EVTP450PK",
    "PQ981182",
    "WE215011",
    # Batch API unknowns (10)
    "AA2008604",
    "AA2020225XAU",
    "AD43371",
    "AD959419",
    "DS37875",
    "FXCRT9 C",
    "FXD-IPLD4P1575 NW/WS",
    "FXS+S2305",
    "GN46017",
    "WE574402",
]

# Use a fresh session for every product so one DB drop doesn't cascade
ok = 0
fail = 0

client = anthropic.Anthropic(
    api_key=config.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
)

for sku in FIX_SKUS:
    db = SessionLocal()
    try:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            print(f"[SKIP] {sku}: not found in DB")
            fail += 1
            db.close()
            continue

        # Find or create a "fix" run
        fix_run = db.query(Run).filter_by(status="manual", notes="fix_failed").first()
        if not fix_run:
            fix_run = Run(status="manual", notes="fix_failed")
            db.add(fix_run)
            db.flush()
        run_id = fix_run.id

        # Get existing scraped content if available
        enrichment = (
            db.query(Enrichment)
            .filter_by(sku=sku)
            .order_by(Enrichment.created_at.desc())
            .first()
        )
        supplier_content = enrichment.scraped_content if enrichment else None
        if not supplier_content or not supplier_content.get("status"):
            mpn = getattr(product, "mpn", "") or ""
            supplier_content = scrape_product(sku, product.vendor or "", product.title or "", mpn=mpn)

        has_feed_desc = bool(supplier_content.get("description", "").strip())
        has_brand_url = supplier_content.get("status") in ("success", "cached", "csv_export")
        image_count = len(product.images or [])
        tier = classify_tier(has_feed_desc, has_brand_url, image_count,
                             existing_content=product.existing_content or {}, sku=sku)

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

        system_prompt = _load_prompt("system.txt")
        user_message = _build_user_message(product_data, supplier_content, tier)

        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=_max_tokens(tier),
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        is_valid, parsed, error = validate_claude_response(raw, tier, image_count=image_count)
        if is_valid:
            new_e = Enrichment(
                run_id=run_id,
                product_id=product.id,
                sku=sku,
                status="success",
                tier=tier,
                scrape_status=supplier_content.get("status", "manual"),
                scraped_content=supplier_content,
                claude_input_tokens=input_tokens,
                claude_output_tokens=output_tokens,
                cost_usd=_cost_usd(input_tokens, output_tokens),
                enriched_data=parsed,
                retry_count=0,
                error_message="",
                writeback_status="pending",
            )
            db.add(new_e)
            db.commit()
            print(f"[OK] {sku}: success (tier={tier}, cost=${_cost_usd(input_tokens, output_tokens):.4f})")
            ok += 1
        else:
            print(f"[FAIL] {sku}: validation failed — {error}")
            fail += 1

    except Exception as e:
        print(f"[FAIL] {sku}: {str(e)[:120]}")
        try:
            db.rollback()
        except Exception:
            pass
        fail += 1
    finally:
        db.close()

print()
print("=" * 60)
print(f"FIX RUN COMPLETE: {ok} success / {fail} failed")
print("=" * 60)
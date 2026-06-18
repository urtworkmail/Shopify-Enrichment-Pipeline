"""
fix_remaining.py — enriches the 18 genuinely failed products
by calling Claude and writing the result in a single, fresh session per product.
"""

import anthropic, httpx
from config import config
from database import SessionLocal, Product, Enrichment, Run
from claude_enricher import (
    classify_tier, _load_prompt, _build_user_message,
    _max_tokens, _cost_usd,
)
from validator import validate_claude_response
from scraper import scrape_product

FAILED_SKUS = [
    # JSON parse errors (8)
    "CD365.V49-27", "CSPRZ104", "DS407825", "EVLSCN41000",
    "EVLWCLPC", "EVTP450PK", "PQ981182", "WE215011",
    # Batch API unknowns (10)
    "AA2008604", "AA2020225XAU", "AD43371", "AD959419",
    "DS37875", "FXCRT9 C", "FXD-IPLD4P1575 NW/WS",
    "FXS+S2305", "GN46017", "WE574402",
]

client = anthropic.Anthropic(
    api_key=config.ANTHROPIC_API_KEY,
    timeout=httpx.Timeout(60.0, connect=10.0),
)

ok = 0
fail = 0

for sku in FAILED_SKUS:
    db = SessionLocal()
    try:
        product = db.query(Product).filter_by(sku=sku).first()
        if not product:
            print(f"[SKIP] {sku}: not in DB")
            fail += 1
            db.close()
            continue

        # ---- scrape (or reuse cached) ----
        enrichment = db.query(Enrichment).filter_by(sku=sku).order_by(Enrichment.created_at.desc()).first()
        supplier_content = enrichment.scraped_content if enrichment else None
        if not supplier_content or not supplier_content.get("status"):
            supplier_content = scrape_product(
                sku, product.vendor or "", product.title or "",
                mpn=getattr(product, "mpn", "") or "",
            )

        # ---- tier ----
        has_feed = bool((supplier_content.get("description") or "").strip())
        has_url  = supplier_content.get("status") in ("success", "cached", "csv_export")
        image_count = len(product.images or [])
        tier = classify_tier(has_feed, has_url, image_count,
                             existing_content=product.existing_content or {}, sku=sku)

        # ---- Claude ----
        product_data = {
            "title": product.title or "",
            "brand": product.vendor or "",
            "vendor": product.vendor or "",
            "sku": sku,
            "price": product.price or "",
            "barcode": getattr(product, "barcode", "") or "",
            "existing_content": product.existing_content or {},
            "category_path": "Office Supplies",
            "compatible_with": "Not specified.",
            "rrp": getattr(product, "rrp", "") or "",
            "gtin": getattr(product, "barcode", "") or "",
            "image_count": image_count,
        }

        system = _load_prompt("system.txt")
        user_msg = _build_user_message(product_data, supplier_content, tier)
        resp = client.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=_max_tokens(tier),
            system=system, messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text
        it, ot = resp.usage.input_tokens, resp.usage.output_tokens

        valid, parsed, err = validate_claude_response(raw, tier, image_count=image_count)

        # ---- create new enrichment row in a manual run ----
        manual_run = db.query(Run).filter_by(status="manual", notes="fix_failed").first()
        if not manual_run:
            manual_run = Run(status="manual", notes="fix_failed")
            db.add(manual_run)
            db.flush()

        new_e = Enrichment(
            run_id=manual_run.id,
            product_id=product.id,
            sku=sku,
            status="success" if valid else "failed",
            tier=tier,
            scrape_status=supplier_content.get("status", "manual"),
            scraped_content=supplier_content,
            claude_input_tokens=it,
            claude_output_tokens=ot,
            cost_usd=_cost_usd(it, ot),
            enriched_data=parsed if valid else None,
            error_message="" if valid else (err or "Validation failed"),
            writeback_status="pending",
        )
        db.add(new_e)
        db.commit()

        if valid:
            print(f"[OK] {sku}: tier={tier}, cost=${_cost_usd(it, ot):.4f}")
            ok += 1
        else:
            print(f"[FAIL] {sku}: {err}")
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

print(f"\nDONE: {ok} success / {fail} failed")
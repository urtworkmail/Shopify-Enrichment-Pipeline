"""
export_qa_csv.py — Export enriched data for QA sample SKUs to a CSV file.
No Shopify writes. Safe for client review.
"""
import csv, json
from database import SessionLocal, Product, Enrichment

QA_SKUS = [
    "PQ120073", "AA6502", "FXPIN129 BE", "PEN50-A",
    "CD10210", "KC4440", "DODKTL0044", "AD13000",
    "SR100-10B", "CU241523", "WE424001", "ECJUMBOLS",
    "FM53218", "AP108829", "CO328F-WHITE", "ZN212",
    "JA0382020", "RHWS30MSE", "3M70005126399",
]

db = SessionLocal()

with open("output/qa_sample_export.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow([
        "SKU", "Title (Enriched)", "Tier", "Scrape Status",
        "SEO Title", "SEO Description",
        "Body HTML (first 500 chars)", "Key Features", "Applications",
        "Specifications (first 5)", "Tags", "Pack Size", "Unit",
        "Image Alt Texts", "Google Category ID",
        "Has Supplier Data?", "Enrichment Cost"
    ])

    for sku in QA_SKUS:
        product = db.query(Product).filter_by(sku=sku).first()
        enrichment = db.query(Enrichment).filter_by(
            sku=sku, status="success"
        ).order_by(Enrichment.created_at.desc()).first()

        if not product or not enrichment:
            writer.writerow([sku, "NOT FOUND", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""])
            continue

        data = enrichment.enriched_data or {}
        scraped = enrichment.scraped_content or {}
        has_supplier = bool(
            (scraped.get("description") or "").strip() or
            (scraped.get("specifications") or "").strip()
        )

        alt_texts = data.get("image_alt_texts", [])
        if isinstance(alt_texts, list):
            alt_str = " | ".join(alt_texts[:5])
        elif isinstance(alt_texts, dict):
            alt_str = " | ".join([alt_texts.get(k, "") for k in ["hero", "lifestyle_1", "lifestyle_2"] if alt_texts.get(k)])
        else:
            alt_str = str(alt_texts)[:200]

        specs = data.get("specifications", [])
        specs_str = " | ".join(specs[:5]) if isinstance(specs, list) else str(specs)[:200]

        key_features = data.get("key_features", [])
        kf_str = " | ".join(key_features) if isinstance(key_features, list) else str(key_features)[:200]

        applications = data.get("applications", [])
        app_str = " | ".join(applications) if isinstance(applications, list) else str(applications)[:200]

        tags = data.get("tags", [])
        tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)[:200]

        writer.writerow([
            sku,
            data.get("title", product.title or "")[:150],
            enrichment.tier or "",
            enrichment.scrape_status or "",
            data.get("seo_title", "")[:80],
            data.get("seo_description", "")[:200],
            (data.get("body_html", "") or "")[:500],
            kf_str[:300],
            app_str[:300],
            specs_str[:300],
            tags_str[:200],
            data.get("pack_size", "")[:30],
            data.get("unit", "")[:20],
            alt_str[:300],
            data.get("google_product_category_id", ""),
            "YES" if has_supplier else "NO",
            f"${enrichment.cost_usd:.4f}" if enrichment.cost_usd else "",
        ])

db.close()
print("Exported to output/qa_sample_export.csv")
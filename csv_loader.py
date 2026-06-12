"""
csv_loader.py -- Loads the Matrixify/Shopify product export CSV into the DB.

Handles the actual Shopify export column names (from the sample):
    ID, Handle, Title, Body HTML, Vendor, Type, Status, Variant SKU,
    Variant Price, Variant Compare At Price, Variant Cost,
    Metafield: title_tag, Metafield: description_tag,
    Metafield: custom.pack_size, Metafield: custom.unit,
    Metafield: custom.faqs, Metafield: custom.applications,
    Metafield: custom.key_features, Metafield: custom.specifications,
    Image Src, Image Alt Text, Variant Barcode
    ...and many more Shopify metafield columns

Also handles the lightweight 200-product sample CSV:
    product_id, sku, supplier_prefix, brand, title, image_count, multi_image, status

Usage:
    python csv_loader.py --file products_export.csv
    python csv_loader.py --file sample_products_200_balanced.csv --sample
    python csv_loader.py --file products_export.csv --limit 100
"""

import argparse
import csv
import json
from pathlib import Path
from typing import Optional

from database import get_db, upsert_product, init_db
from config import config


def _parse_price(value: str) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_tags(value: str) -> list[str]:
    if not value:
        return []
    return [t.strip() for t in str(value).split(",") if t.strip()]


def _is_sample_csv(headers: list[str]) -> bool:
    """Detect the lightweight 200-product sample format."""
    return "supplier_prefix" in headers and "brand" in headers and "image_count" in headers


def _load_sample_csv(filepath: str, limit: Optional[int] = None) -> int:
    """Load the lightweight 200-product sample format."""
    count = 0
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        with get_db() as db:
            for row in reader:
                sku = row.get("sku", "").strip()
                if not sku:
                    continue
                data = {
                    "sku": sku,
                    "shopify_product_id": f"gid://shopify/Product/{row.get('product_id', '')}",
                    "title": row.get("title", "").strip(),
                    "vendor": row.get("brand", "").strip(),
                    "images": [],
                    "image_count": int(row.get("image_count", 0) or 0),
                    "raw_csv_data": dict(row),
                }
                upsert_product(db, data)
                count += 1
                if limit and count >= limit:
                    break
    print(f"[csv] Sample CSV: loaded {count} products.")
    return count


def _load_matrixify_csv(filepath: str, limit: Optional[int] = None) -> int:
    """Load full Matrixify/Shopify export format."""
    products_raw: dict[str, dict] = {}

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])

        for row in reader:
            sku = row.get("Variant SKU", "").strip()
            if not sku:
                continue

            if sku not in products_raw:
                price = _parse_price(row.get("Variant Price", "") or "")
                compare_at = _parse_price(row.get("Variant Compare At Price", "") or "")
                cost = _parse_price(row.get("Variant Cost", "") or "")

                # Shopify GID from ID column
                raw_id = row.get("ID", "").strip()
                shopify_id = (
                    raw_id if raw_id.startswith("gid://") else
                    f"gid://shopify/Product/{raw_id}" if raw_id else ""
                )

                products_raw[sku] = {
                    "sku": sku,
                    "shopify_product_id": shopify_id or None,
                    "title": row.get("Title", "").strip(),
                    "vendor": row.get("Vendor", "").strip() or None,
                    "product_type": row.get("Type", "").strip() or None,
                    "tags": _parse_tags(row.get("Tags", "")),
                    "handle": row.get("Handle", "").strip() or None,
                    "description_html": row.get("Body HTML", "").strip() or None,
                    "price": price,
                    "rrp": compare_at,
                    "cost": cost,
                    "barcode": row.get("Variant Barcode", "").strip() or None,
                    "supplier_code": row.get(
                        "Metafield: custom.supplier_code [single_line_text_field]", ""
                    ).strip() or None,
                    "mpn": row.get(
                        "Metafield: custom.mpn [single_line_text_field]", ""
                    ).strip() or None,
                    "upc": row.get(
                        "Metafield: custom.upc [single_line_text_field]", ""
                    ).strip() or None,
                    "images": [],
                    "raw_csv_data": dict(row),
                }

            # Collect images
            img_src = row.get("Image Src", "").strip()
            img_alt = row.get("Image Alt Text", "").strip()
            existing_urls = [i["url"] for i in products_raw[sku]["images"]]
            if img_src and img_src not in existing_urls:
                products_raw[sku]["images"].append({
                    "url": img_src,
                    "altText": img_alt,
                    "id": None,
                })

            if limit and len(products_raw) >= limit:
                break

    count = 0
    with get_db() as db:
        for sku, data in products_raw.items():
            data["image_count"] = len(data.get("images", []))
            upsert_product(db, data)
            count += 1
            if count % 1000 == 0:
                print(f"[csv] Upserted {count} products ...")

    print(f"[csv] Matrixify export: loaded {count} products.")
    return count


def load_csv(filepath: str, limit: Optional[int] = None, force_sample: bool = False) -> int:
    """Auto-detect CSV format and load into DB."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {filepath}")

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])

    if force_sample or _is_sample_csv(headers):
        print(f"[csv] Detected sample CSV format.")
        return _load_sample_csv(filepath, limit)
    else:
        print(f"[csv] Detected Matrixify export format ({len(headers)} columns).")
        return _load_matrixify_csv(filepath, limit)


def print_summary(filepath: str):
    """Print a quick pre-load summary."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    is_sample = _is_sample_csv(headers)
    skus = set()
    for row in rows:
        sku = row.get("Variant SKU", row.get("sku", "")).strip()
        if sku:
            skus.add(sku)

    print("\n" + "=" * 50)
    print("CSV SUMMARY")
    print("=" * 50)
    print(f"  Format:              {'Sample (200-product)' if is_sample else 'Matrixify export'}")
    print(f"  Total rows:          {len(rows)}")
    print(f"  Unique SKUs:         {len(skus)}")
    print(f"  Columns:             {len(headers)}")
    if is_sample:
        multi = sum(1 for r in rows if r.get("multi_image", "").lower() == "yes")
        print(f"  Multi-image:         {multi}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load Shopify product CSV into DB")
    parser.add_argument("--file", default=config.INPUT_CSV)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sample", action="store_true", help="Force sample CSV mode")
    parser.add_argument("--summary", action="store_true", help="Summary only, no load")
    args = parser.parse_args()

    init_db()
    print_summary(args.file)
    if not args.summary:
        load_csv(args.file, limit=args.limit, force_sample=args.sample)

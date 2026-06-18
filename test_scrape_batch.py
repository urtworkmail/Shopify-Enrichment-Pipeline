"""
test_scrape_batch.py -- Run a test scrape on 2 products from each supplier
to verify the scraper works correctly before full run.

Usage:
    python test_scrape_batch.py [--output OUTPUT_CSV]

Outputs a CSV with results for client review.
"""
import csv
import sys
import time
from pathlib import Path

from database import SessionLocal, Product
from scraper import scrape_product, init_scraper, SUPPLIER_SEARCH_CONFIG
from supplier_router import load_supplier_map

# Config
OUTPUT_CSV = "output/test_scrape_results.csv"
PRODUCTS_PER_PREFIX = 2


def get_prefixes_with_config():
    """Get list of prefixes that have a working search config."""
    # Load the supplier map
    load_supplier_map("prefix_supplier_map_FINAL.csv")

    from supplier_router import _SUPPLIER_MAP, _IGNORE_PREFIXES, _MULTI_BRAND_PREFIXES

    configured_prefixes = []

    for prefix, info in _SUPPLIER_MAP.items():
        if prefix in _IGNORE_PREFIXES:
            continue

        domain = info.get("supplier_domain", "").split("|")[0].split(";")[0].strip()
        if domain.startswith("http"):
            from urllib.parse import urlparse
            domain = urlparse(domain).netloc

        # Check if we have a config for this domain
        if domain in SUPPLIER_SEARCH_CONFIG:
            configured_prefixes.append({
                "prefix": prefix,
                "domain": domain,
                "supplier_name": info.get("supplier_name", ""),
            })
        # Also check multi-brand prefixes (they route by brand)
        elif prefix in _MULTI_BRAND_PREFIXES:
            configured_prefixes.append({
                "prefix": prefix,
                "domain": f"MULTI-BRAND ({prefix})",
                "supplier_name": info.get("supplier_name", ""),
            })

    return configured_prefixes


def get_sample_products(prefix: str, count: int):
    """Get sample products for a given prefix."""
    db = SessionLocal()
    try:
        products = db.query(Product).filter(
            Product.sku.like(f"{prefix}%")
        ).limit(count).all()

        return [
            {
                "sku": p.sku,
                "title": p.title or "",
                "vendor": p.vendor or "",
                "price": p.price,
            }
            for p in products
        ]
    finally:
        db.close()


def main():
    print("=" * 60)
    print("SCRAPER TEST BATCH")
    print("=" * 60)

    # Initialize scraper
    print("\nInitializing scraper...")
    init_scraper()

    # Get configured prefixes
    configured = get_prefixes_with_config()
    print(f"\nConfigured suppliers: {len(configured)}")

    # Collect all test products
    test_products = []
    for cfg in configured:
        prefix = cfg["prefix"]
        products = get_sample_products(prefix, PRODUCTS_PER_PREFIX)
        if products:
            for p in products:
                p["prefix"] = prefix
                p["domain"] = cfg["domain"]
                p["supplier_name"] = cfg["supplier_name"]
                test_products.append(p)
            print(f"  {prefix} ({cfg['domain']}): {len(products)} products")
        else:
            print(f"  {prefix}: NO PRODUCTS FOUND")

    print(f"\nTotal test products: {len(test_products)}")

    # Run scrape on each product
    results = []
    print("\nRunning scrape tests...")
    for i, p in enumerate(test_products, 1):
        print(f"[{i}/{len(test_products)}] {p['sku']} ({p['domain']})...", end=" ", flush=True)

        try:
            result = scrape_product(
                p["sku"],
                p["vendor"],
                p["title"]
            )
        except Exception as e:
            result = {
                "status": f"error: {str(e)[:50]}",
                "description": "",
                "specifications": "",
                "features": "",
            }

        # Extract key info
        status = result.get("status", "unknown")
        has_content = bool(result.get("description", "").strip() or result.get("specifications", "").strip())

        print(f"{status}" + (" [HAS CONTENT]" if has_content else " [EMPTY]"))

        results.append({
            "sku": p["sku"],
            "prefix": p["prefix"],
            "domain": p["domain"],
            "title": p["title"][:50] if p["title"] else "",
            "vendor": p["vendor"],
            "scrape_status": status,
            "routing_strategy": result.get("routing_strategy", ""),
            "has_description": bool(result.get("description", "").strip()),
            "has_specs": bool(result.get("specifications", "").strip()),
            "description_preview": (result.get("description", "") or "")[:100],
            "diagnostic": result.get("diagnostic", {}),
        })

        # Small delay between requests
        time.sleep(0.5)

    # Save results
    Path("output").mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "sku", "prefix", "domain", "title", "vendor",
            "scrape_status", "routing_strategy",
            "has_description", "has_specs", "description_preview"
        ])
        writer.writeheader()
        for r in results:
            # Flatten for CSV
            row = {k: v for k, v in r.items() if k != "diagnostic"}
            writer.writerow(row)

    print(f"\n{'=' * 60}")
    print(f"Results saved to: {OUTPUT_CSV}")

    # Summary
    success = sum(1 for r in results if r["scrape_status"] == "success")
    cached = sum(1 for r in results if r["scrape_status"] == "cached")
    failed = sum(1 for r in results if r["scrape_status"] not in ("success", "cached"))
    has_content = sum(1 for r in results if r["has_description"] or r["has_specs"])

    print(f"SUMMARY:")
    print(f"  Success: {success}")
    print(f"  Cached: {cached}")
    print(f"  Failed/Not Found: {failed}")
    print(f"  Got Content: {has_content}/{len(results)}")
    print(f"{'=' * 60}")

    return results


if __name__ == "__main__":
    main()
"""
shopify_fetch.py -- Fetches all product data from Shopify via Bulk Operations API.
Merges results into existing DB products (updates shopify_product_id and images).
"""

import json
import time
from pathlib import Path

import requests

from config import config
from token_manager import get_headers
from database import get_db, Product

BULK_QUERY_MUTATION = """
mutation {
  bulkOperationRunQuery(
    query: \"\"\"
    {
      products {
        edges {
          node {
            id
            title
            descriptionHtml
            tags
            handle
            vendor
            productType
            priceRangeV2 {
              minVariantPrice { amount currencyCode }
            }
            seo { title description }
            images(first: 10) {
              edges {
                node {
                  id
                  url
                  altText
                }
              }
            }
            variants(first: 5) {
              edges {
                node {
                  id
                  sku
                  price
                }
              }
            }
            metafields(first: 20) {
              edges {
                node {
                  namespace
                  key
                  value
                  type
                }
              }
            }
          }
        }
      }
    }
    \"\"\"
  ) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
"""

POLL_QUERY = """
query {
  currentBulkOperation {
    id status errorCode objectCount fileSize url partialDataUrl
  }
}
"""


def _post(payload: dict) -> dict:
    resp = requests.post(
        config.shopify_graphql_url,
        headers=get_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def start_bulk_query() -> str:
    data = _post({"query": BULK_QUERY_MUTATION})
    errors = data.get("data", {}).get("bulkOperationRunQuery", {}).get("userErrors", [])
    if errors:
        raise RuntimeError(f"Bulk query start errors: {errors}")
    op = data["data"]["bulkOperationRunQuery"]["bulkOperation"]
    print(f"[fetch] Bulk query started. ID: {op['id']} | Status: {op['status']}")
    return op["id"]


def poll_until_complete(timeout_seconds: int = 7200) -> dict:
    elapsed = 0
    while elapsed < timeout_seconds:
        time.sleep(config.POLL_INTERVAL_SECONDS)
        elapsed += config.POLL_INTERVAL_SECONDS
        op = _post({"query": POLL_QUERY})["data"]["currentBulkOperation"]
        status = op["status"]
        print(f"[fetch] Poll: {status} | objects={op.get('objectCount','?')} | {elapsed}s elapsed")
        if status == "COMPLETED":
            return op
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Bulk operation {status}: {op.get('errorCode')}")
    raise TimeoutError("Bulk query timed out.")


def download_jsonl(url: str, dest: str = "output/products_raw.jsonl", max_retries: int = 3) -> str:
    Path("output").mkdir(parents=True, exist_ok=True)
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, stream=True, timeout=(30, 300))
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            print(f"[fetch] Downloaded to {dest}")
            return dest
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.Timeout) as e:
            print(f"[fetch] Download attempt {attempt} failed: {e}")
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)


def merge_into_db(jsonl_path: str) -> int:
    """
    Parse the bulk query JSONL and merge Shopify IDs + images into DB products.
    Matches on SKU from variants.
    """
    products_by_id: dict[str, dict] = {}
    sku_to_shopify: dict[str, dict] = {}

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "__parentId" not in rec:
                rec.setdefault("_images", [])
                rec.setdefault("_variants", [])
                rec.setdefault("_metafields", [])
                products_by_id[rec["id"]] = rec

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            pid = rec.get("__parentId")
            if not pid or pid not in products_by_id:
                continue
            parent = products_by_id[pid]
            if "sku" in rec:
                parent["_variants"].append(rec)
                if rec["sku"]:
                    sku_to_shopify[rec["sku"]] = parent
            elif "url" in rec:
                parent["_images"].append(rec)
            elif "namespace" in rec:
                parent["_metafields"].append(rec)

    updated = 0
    with get_db() as db:
        for sku, shopify_product in sku_to_shopify.items():
            product = db.query(Product).filter_by(sku=sku).first()
            if product:
                product.shopify_product_id = shopify_product["id"]
                product.raw_shopify_data = shopify_product
                product.images = [
                    {"id": img.get("id"), "url": img["url"], "altText": img.get("altText", "")}
                    for img in shopify_product.get("_images", [])
                ]
                raw_metafields = shopify_product.get("_metafields", [])
                product.metafields = raw_metafields

                # Addendum 3.1 Section E: extract each existing field individually
                # so Claude gets them as separate named variables, not a merged blob.
                # Keys: custom.body_html, custom.key_features, custom.applications,
                #       custom.specifications, custom.pack_size, custom.unit
                # Also capture global.title_tag for SEO continuity.
                existing = {}
                for mf in raw_metafields:
                    ns  = mf.get("namespace", "")
                    key = mf.get("key", "")
                    val = mf.get("value", "")
                    if val:
                        existing[f"{ns}.{key}"] = val

                # Body HTML lives on the product record, not in metafields -- store it too
                if shopify_product.get("descriptionHtml"):
                    existing["description_html"] = shopify_product["descriptionHtml"]

                product.existing_content = existing
                updated += 1

    print(f"[fetch] Merged Shopify data for {updated} products.")
    return updated


def fetch_and_merge(use_cache: bool = False, cache_path: str = "output/products_raw.jsonl") -> int:
    """Full fetch pipeline. Returns number of products merged into DB."""
    if use_cache and Path(cache_path).exists():
        print(f"[fetch] Using cached JSONL: {cache_path}")
        return merge_into_db(cache_path)

    start_bulk_query()
    op = poll_until_complete()
    if not op.get("url"):
        raise RuntimeError("Bulk operation returned no download URL.")
    download_jsonl(op["url"], cache_path)
    return merge_into_db(cache_path)

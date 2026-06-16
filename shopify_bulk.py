"""
shopify_bulk.py -- Shopify Bulk Operations write-back.

Three separate bulk passes per handoff doc Section 6.3:
    Pass A: productUpdate  (title, descriptionHtml, tags, seo, vendor, productType -- NO status)
    Pass B: metafieldsSet  (all custom.* metafields + mm-google-shopping.*)
    Pass C: fileUpdate     (image alt text + SEO filename rename)

Key corrections from handoff:
    - Mutation signature: productUpdate(product: ProductUpdateInput!) -- NOT input: ProductInput!
    - SEO goes in seo { title description } inside productUpdate -- NOT as global.* metafields
    - Status is NEVER written -- leave products as-is (already live/active)
    - metafields are a separate bulk pass, not inline with productUpdate
    - One list field per bulk mutation to stay within Shopify bulk limits
"""

import json
import time
from pathlib import Path

import requests

from config import config
from token_manager import get_headers
from database import get_db, Enrichment, Product
from validator import prepare_metafields, slugify_filename

# ── Mutation strings ──────────────────────────────────────────────────────────

# Pass A: core product fields
PRODUCT_UPDATE_MUTATION = """
mutation call($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product { id title }
    userErrors { field message }
  }
}
"""

# Pass B: all metafields
METAFIELDS_SET_MUTATION = """
mutation call($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id namespace key value }
    userErrors { field message }
  }
}
"""

# Pass C: image alt text + filename
FILE_UPDATE_MUTATION = """
mutation call($files: [FileUpdateInput!]!) {
  fileUpdate(files: $files) {
    files { id alt fileStatus }
    userErrors { field message }
  }
}
"""

BULK_RUN_MUTATION = """
mutation BulkRun($mutation: String!, $stagedUploadPath: String!) {
  bulkOperationRunMutation(mutation: $mutation, stagedUploadPath: $stagedUploadPath) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
"""

STAGE_UPLOAD_MUTATION = """
mutation StagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets {
      url
      resourceUrl
      parameters { name value }
    }
    userErrors { field message }
  }
}
"""

POLL_QUERY = """
query {
  currentBulkOperation(type: MUTATION) {
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


# ── JSONL builders ────────────────────────────────────────────────────────────

def build_product_jsonl_line(product: Product, enriched: dict) -> str:
    """Pass A line: title, descriptionHtml, tags, seo, vendor. Status intentionally omitted."""
    alt_texts = enriched.get("image_alt_texts", {})
    return json.dumps({
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
    })


def build_metafields_jsonl_line(product: Product, enriched: dict) -> str:
    """Pass B line: all custom.* and mm-google-shopping.* metafields."""
    metafields = prepare_metafields(enriched, sku=product.sku)
    # Attach owner ID to each metafield
    for mf in metafields:
        mf["ownerId"] = product.shopify_product_id
    return json.dumps({"metafields": metafields})


def build_file_jsonl_line(image: dict, product_title: str, position: int,
                           alt_text: str) -> Optional[str]:
    """Pass C line: image alt text + SEO filename. Skip if image has no ID."""
    img_id = image.get("id")
    if not img_id:
        return None
    return json.dumps({
        "files": [{
            "id": img_id,
            "alt": alt_text,
            "filename": slugify_filename(product_title, position),
        }]
    })


# ── Write JSONL files from DB ─────────────────────────────────────────────────

def write_all_jsonl_from_db(run_id: int) -> tuple[str, str, str]:
    """
    Build all three JSONL files for bulk passes A, B, C.
    Returns (product_path, metafields_path, files_path).
    """
    Path("output").mkdir(parents=True, exist_ok=True)
    product_path = "output/bulk_a_products.jsonl"
    metafields_path = "output/bulk_b_metafields.jsonl"
    files_path = "output/bulk_c_files.jsonl"

    count = 0
    with get_db() as db, \
         open(product_path, "w", encoding="utf-8") as pa, \
         open(metafields_path, "w", encoding="utf-8") as pb, \
         open(files_path, "w", encoding="utf-8") as pc:

        enrichments = (
            db.query(Enrichment)
            .filter_by(run_id=run_id, status="success", writeback_status="pending")
            .all()
        )

        for enrichment in enrichments:
            product = db.query(Product).filter_by(id=enrichment.product_id).first()
            if not product or not product.shopify_product_id:
                continue

            enriched = enrichment.enriched_data or {}
            title = enriched.get("title", product.title or "")

            # Pass A
            pa.write(build_product_jsonl_line(product, enriched) + "\n")

            # Pass B
            mf_line = build_metafields_jsonl_line(product, enriched)
            if mf_line:
                pb.write(mf_line + "\n")

            # Pass C
            alt_texts_raw = enriched.get("image_alt_texts", {})
            alt_list = []
            if isinstance(alt_texts_raw, dict):
                alt_list = [
                    alt_texts_raw.get("hero", ""),
                    alt_texts_raw.get("lifestyle_1", ""),
                    alt_texts_raw.get("lifestyle_2", ""),
                ]
            elif isinstance(alt_texts_raw, list):
                alt_list = alt_texts_raw

            for i, image in enumerate(product.images or []):
                alt = alt_list[i] if i < len(alt_list) else ""
                line = build_file_jsonl_line(image, title, i + 1, alt or "")
                if line:
                    pc.write(line + "\n")

            count += 1

    print(f"[bulk] Built JSONL for {count} products:")
    print(f"  Pass A (products):    {product_path}")
    print(f"  Pass B (metafields):  {metafields_path}")
    print(f"  Pass C (files):       {files_path}")
    return product_path, metafields_path, files_path


# ── Upload and submit ─────────────────────────────────────────────────────────

def _stage_upload(filename: str) -> tuple[str, str, list]:
    payload = {
        "query": STAGE_UPLOAD_MUTATION,
        "variables": {"input": [{
            "filename": Path(filename).name,
            "mimeType": "text/jsonl",
            "httpMethod": "POST",
            "resource": "BULK_MUTATION_VARIABLES",
        }]},
    }
    result = _post(payload)
    errors = result["data"]["stagedUploadsCreate"]["userErrors"]
    if errors:
        raise RuntimeError(f"Stage upload errors: {errors}")
    target = result["data"]["stagedUploadsCreate"]["stagedTargets"][0]
    return target["url"], target["resourceUrl"], target["parameters"]


def _upload_file(url: str, params: list, filepath: str):
    import requests_toolbelt
    fields = {p["name"]: p["value"] for p in params}
    with open(filepath, "rb") as f:
        fields["file"] = (Path(filepath).name, f, "text/jsonl")
        encoder = requests_toolbelt.MultipartEncoder(fields=fields)
        resp = requests.post(
            url, data=encoder,
            headers={"Content-Type": encoder.content_type},
            timeout=120,
        )
    resp.raise_for_status()


def submit_bulk_pass(jsonl_path: str, mutation_string: str, pass_name: str) -> str:
    """Stage, upload and submit one bulk pass. Returns operation ID."""
    print(f"[bulk] Pass {pass_name}: staging {jsonl_path}")
    upload_url, resource_url, params = _stage_upload(jsonl_path)
    _upload_file(upload_url, params, jsonl_path)

    result = _post({
        "query": BULK_RUN_MUTATION,
        "variables": {
            "mutation": mutation_string,
            "stagedUploadPath": resource_url,
        },
    })
    errors = result["data"]["bulkOperationRunMutation"]["userErrors"]
    if errors:
        raise RuntimeError(f"Bulk pass {pass_name} submit errors: {errors}")
    op = result["data"]["bulkOperationRunMutation"]["bulkOperation"]
    print(f"[bulk] Pass {pass_name} job started: {op['id']} | {op['status']}")
    return op["id"]


def poll_until_complete(pass_name: str, timeout_seconds: int = 14400) -> dict:
    """Poll current bulk mutation operation until complete."""
    elapsed = 0
    while elapsed < timeout_seconds:
        time.sleep(config.POLL_INTERVAL_SECONDS)
        elapsed += config.POLL_INTERVAL_SECONDS
        op = _post({"query": POLL_QUERY})["data"]["currentBulkOperation"]
        status = op["status"]
        print(f"[bulk] Pass {pass_name}: {status} | objects={op.get('objectCount','?')} | {elapsed}s")
        if status == "COMPLETED":
            return op
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Bulk pass {pass_name} {status}: {op.get('errorCode')}")
    raise TimeoutError(f"Bulk pass {pass_name} timed out after {timeout_seconds}s")


def download_and_record_errors(url: str, run_id: int, dest: str) -> int:
    """Download bulk result JSONL, update writeback_status in DB. Returns error count."""
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)

    error_count = 0
    with get_db() as db, open(dest, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line.strip())
            shopify_id = rec.get("id") or (
                rec.get("product", {}) or {}
            ).get("id", "")
            user_errors = rec.get("userErrors", [])

            if shopify_id:
                enrichment = (
                    db.query(Enrichment)
                    .join(Product)
                    .filter(
                        Product.shopify_product_id == shopify_id,
                        Enrichment.run_id == run_id,
                    )
                    .first()
                )
                if enrichment:
                    if user_errors:
                        enrichment.writeback_status = "failed"
                        enrichment.writeback_error = str(user_errors)
                        error_count += 1
                    else:
                        enrichment.writeback_status = "success"

    print(f"[bulk] Result parsed. Errors: {error_count}")
    return error_count


# ── Full write-back orchestrator ──────────────────────────────────────────────

def run_all_bulk_passes(run_id: int):
    """
    Execute all three bulk passes in order.
    Pass A must complete before Pass B starts (Shopify allows one bulk op at a time
    on older API versions).
    """
    product_path, metafields_path, files_path = write_all_jsonl_from_db(run_id)

    # Pass C first -- file renames must happen before Google Merchant feed goes live
    print("\n[bulk] Starting Pass C: file updates (alt text + filename rename)")
    submit_bulk_pass(files_path, FILE_UPDATE_MUTATION, "C")
    op = poll_until_complete("C")
    if op.get("url"):
        download_and_record_errors(op["url"], run_id, "output/result_c_files.jsonl")

    # Pass A: core product fields
    print("\n[bulk] Starting Pass A: product content (title, description, SEO, tags)")
    submit_bulk_pass(product_path, PRODUCT_UPDATE_MUTATION, "A")
    op = poll_until_complete("A")
    if op.get("url"):
        download_and_record_errors(op["url"], run_id, "output/result_a_products.jsonl")

    # Pass B: metafields
    print("\n[bulk] Starting Pass B: metafields")
    submit_bulk_pass(metafields_path, METAFIELDS_SET_MUTATION, "B")
    op = poll_until_complete("B")
    if op.get("url"):
        download_and_record_errors(op["url"], run_id, "output/result_b_metafields.jsonl")

    print("\n[bulk] All three passes complete.")

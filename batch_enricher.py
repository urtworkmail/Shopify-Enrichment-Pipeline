"""
batch_enricher.py -- Anthropic Batch API enrichment.
Submits a JSONL of Claude requests, polls until complete,
downloads results, and merges into the DB.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import requests
from config import config
from database import get_db, Enrichment, Run


def submit_batch(items: list[tuple]) -> dict:
    """
    Build and submit a batch of Claude requests.
    items: list of (enrichment_id, product_data, supplier_content, tier)
    Returns the decoded JSON of the batch creation response.
    """
    from claude_enricher import _load_prompt, _build_user_message, _max_tokens

    requests_list = []
    for enrichment_id, product_data, supplier_content, tier in items:
        user_message = _build_user_message(product_data, supplier_content, tier)
        system_prompt = _load_prompt("system.txt")
        requests_list.append({
            "custom_id": str(enrichment_id),
            "params": {
                "model": config.CLAUDE_MODEL,
                "max_tokens": _max_tokens(tier),
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            }
        })

    # Submit as JSON body — the Batch API expects {"requests": [...]}
    url = "https://api.anthropic.com/v1/messages/batches"
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {"requests": requests_list}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    print(f"[batch] Status: {resp.status_code}")
    if resp.status_code >= 400:
        print(f"[batch] Response: {resp.text}")
    resp.raise_for_status()
    batch = resp.json()
    print(f"[batch] Submitted: {batch.get('id')}")
    return batch

def poll_batch(batch_id: str) -> dict:
    """Poll an Anthropic batch until it completes. Returns the final batch object."""
    url = f"https://api.anthropic.com/v1/messages/batches/{batch_id}"
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }
    while True:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        status = batch.get("processing_status")
        print(f"[batch] {batch_id}: {status}")
        if status == "ended":
            return batch
        time.sleep(15)


def download_and_merge(batch: dict, run_id: int):
    """Download batch results JSONL and merge into DB."""
    from claude_enricher import _cost_usd
    from validator import validate_claude_response

    results_url = batch.get("results_url")
    if not results_url:
        print("[batch] No results URL in batch response.")
        return

    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }
    resp = requests.get(results_url, headers=headers, timeout=60)
    if resp.status_code >= 400:
        print(f"[batch] Download error: {resp.status_code} {resp.text}")
    resp.raise_for_status()
    lines = resp.text.strip().split("\n")
    # rest is unchanged …

    with get_db() as db:
        run = db.query(Run).filter_by(id=run_id).first()
        for line in lines:
            rec = json.loads(line)
            custom_id = rec.get("custom_id")
            result = rec.get("result", {})

            enrichment = db.query(Enrichment).filter_by(id=int(custom_id)).first()
            if not enrichment:
                continue

            if result.get("type") == "succeeded":
                raw = result["message"]["content"][0]["text"]
                input_tokens = result["message"]["usage"]["input_tokens"]
                output_tokens = result["message"]["usage"]["output_tokens"]
                tier = enrichment.tier or "T2"

                is_valid, parsed, error = validate_claude_response(
                    raw, tier, image_count=getattr(enrichment.product, "image_count", 0) if enrichment.product else 0
                )
                if is_valid:
                    enrichment.status = "success"
                    enrichment.enriched_data = parsed
                    enrichment.claude_input_tokens = input_tokens
                    enrichment.claude_output_tokens = output_tokens
                    enrichment.cost_usd = _cost_usd(input_tokens, output_tokens)
                    enrichment.retry_count = 0
                    enrichment.error_message = ""
                    if run:
                        run.enriched_count = (run.enriched_count or 0) + 1
                        run.total_input_tokens = (run.total_input_tokens or 0) + input_tokens
                        run.total_output_tokens = (run.total_output_tokens or 0) + output_tokens
                        run.estimated_cost_usd = (run.estimated_cost_usd or 0.0) + enrichment.cost_usd
                else:
                    enrichment.status = "failed"
                    enrichment.error_message = error or "Validation failed"
                    enrichment.retry_count = 0
            else:
                enrichment.status = "failed"
                enrichment.error_message = f"Batch API error: {result.get('error', {}).get('message', 'unknown')}"
                enrichment.retry_count = 0

        db.commit()
        print(f"[batch] Merged {len(lines)} results.")


def run_batch_phase(run_id: int, items: list[tuple]):
    """Submit a batch, poll, download, and merge. Synchronous entry point."""
    batch = submit_batch(items)
    batch_id = batch.get("id")
    if not batch_id:
        raise RuntimeError(f"Batch submission failed: {batch}")
    final_batch = poll_batch(batch_id)
    download_and_merge(final_batch, run_id)
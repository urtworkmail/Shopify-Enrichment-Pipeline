"""
validator.py -- Validates Claude JSON output and builds Shopify-ready field values.

Key helpers:
    build_rich_text()   -- converts string[] to Shopify rich_text_field Portable Text JSON
    build_faqs()        -- converts FAQ array to JSON string for custom.faqs
    slugify_filename()  -- generates SEO filename for fileUpdate
    validate_claude_response() -- full schema check on Claude output
"""

import json
import re
from typing import Any, Optional

from config import config

# ── Rich text builder ─────────────────────────────────────────────────────────

def build_rich_text(bullets: list[str]) -> str:
    """
    Convert a list of strings into the Shopify rich_text_field Portable Text JSON string.
    Each string becomes its own list-item node -- never use \\n to fake multiple bullets.
    """
    return json.dumps({
        "type": "root",
        "children": [
            {
                "type": "list",
                "listType": "unordered",
                "children": [
                    {
                        "type": "list-item",
                        "children": [{"type": "text", "value": bullet.strip()}]
                    }
                    for bullet in bullets if bullet.strip()
                ]
            }
        ]
    })


def build_faqs(faqs: list[dict]) -> str:
    """Serialize FAQ array to JSON string for custom.faqs (json type)."""
    return json.dumps(faqs)


def slugify_filename(product_title: str, position: int,
                     store_suffix: str = "Mega_Office_Supplies") -> str:
    """
    Build an SEO-friendly image filename from product title.
    e.g. 'Brother LC-536XLY High Yield Yellow Ink Cartridge'
      -> 'Brother_LC536XLY_High_Yield_Yellow_Ink_Cartridge_Mega_Office_Supplies_1.jpg'
    """
    slug = re.sub(r"[^\w\s]", "", product_title)
    slug = re.sub(r"\s+", "_", slug.strip())
    slug = slug[:80]
    return f"{slug}_{store_suffix}_{position}.jpg"


# ── Schema validation ─────────────────────────────────────────────────────────

T1_REQUIRED = [
    "title", "body_html", "seo_title", "seo_description",
    "key_features", "applications", "specifications", "faqs",
    "tags", "pack_size", "unit", "google_product_category_id", "image_alt_texts",
]
T2_REQUIRED = [
    "title", "body_html", "seo_title", "seo_description",
    "key_features", "applications", "specifications",
    "tags", "pack_size", "unit", "image_alt_texts",
]
T3_REQUIRED = [
    "title", "body_html", "seo_title", "seo_description",
    "key_features", "applications", "specifications",
    "tags", "pack_size", "unit",
]

TIER_REQUIRED = {"T1": T1_REQUIRED, "T2": T2_REQUIRED, "T3": T3_REQUIRED}

# Banned content per governance doc
BANNED_PHRASES = [
    "look no further", "this product is", "this item is",
    "featuring", "designed with", "designed for",
    "in today's fast-paced world", "whether you are",
    "state of the art", "world class", "industry leading",
]
BANNED_CHARS = ["\u2014", "\u2013", "\u201c", "\u201d", "\u2018", "\u2019", "\u2026"]


def validate_claude_response(raw: str, tier: str) -> tuple[bool, Optional[dict], str]:
    """
    Parse and validate Claude's JSON response.
    Returns (is_valid, parsed_dict, error_message).
    """
    # Strip markdown fences
    clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE)

    try:
        data = json.loads(clean)
    except json.JSONDecodeError as e:
        return False, None, f"JSON parse error: {e}"

    required = TIER_REQUIRED.get(tier, T3_REQUIRED)
    missing = [f for f in required if f not in data]
    if missing:
        return False, None, f"Missing required fields: {missing}"

    # SEO title length
    seo_title = data.get("seo_title", "")
    if len(seo_title) > 60:
        return False, None, f"seo_title too long: {len(seo_title)} chars (max 60)"

    # SEO title must end with | Mega Office Supplies
    if seo_title and "Mega Office Supplies" not in seo_title:
        return False, None, "seo_title missing '| Mega Office Supplies' suffix"

    # Meta description length
    seo_desc = data.get("seo_description", "")
    if seo_desc:
        if len(seo_desc) < 80:
            return False, None, f"seo_description too short: {len(seo_desc)} chars (min 80)"
        if len(seo_desc) > 160:
            return False, None, f"seo_description too long: {len(seo_desc)} chars (max 160)"

    # body_html: no inline styles, no h1, no em dashes
    body = data.get("body_html", "")
    if "style=" in body:
        return False, None, "body_html contains inline styles -- not allowed"
    if "<h1" in body:
        return False, None, "body_html contains <h1> -- not allowed (use <h2> only)"
    for char in BANNED_CHARS:
        if char in body:
            return False, None, f"body_html contains banned character: {repr(char)}"

    # Tags: exactly 10 for T1/T2, minimum 6 for T3
    tags = data.get("tags", [])
    if tier in ("T1", "T2") and len(tags) != 10:
        return False, None, f"tags must have exactly 10 items for {tier}, got {len(tags)}"
    if tier == "T3" and len(tags) < 6:
        return False, None, f"tags must have minimum 6 items for T3, got {len(tags)}"

    # key_features count
    kf = data.get("key_features", [])
    if tier in ("T1", "T2"):
        if not (5 <= len(kf) <= 7):
            return False, None, f"key_features must have 5-7 items for {tier}, got {len(kf)}"

    # FAQs: T1 requires 3-5, T2/T3 must be empty
    faqs = data.get("faqs", [])
    if tier == "T1" and not (3 <= len(faqs) <= 5):
        return False, None, f"T1 requires 3-5 FAQs, got {len(faqs)}"
    if tier in ("T2", "T3") and len(faqs) > 0:
        return False, None, f"{tier} must have empty faqs array, got {len(faqs)}"

    # image_alt_texts structure
    alt = data.get("image_alt_texts", {})
    if not isinstance(alt, dict):
        return False, None, "image_alt_texts must be a dict"
    if not alt.get("hero"):
        return False, None, "image_alt_texts.hero is required"

    return True, data, ""


def prepare_metafields(enriched: dict) -> list[dict]:
    """
    Convert Claude output into Shopify metafield objects ready for metafieldsSet.
    Handles type conversion: lists -> rich_text_field JSON, FAQs -> json string.
    """
    metafields = []

    def mf(namespace: str, key: str, type_: str, value: str) -> dict:
        return {"namespace": namespace, "key": key, "type": type_, "value": value}

    # rich_text_field fields (arrays of strings -> Portable Text JSON)
    for field in ["key_features", "applications", "specifications"]:
        val = enriched.get(field)
        if val and isinstance(val, list):
            metafields.append(mf("custom", field, "rich_text_field", build_rich_text(val)))

    # FAQs (array of objects -> JSON string)
    faqs = enriched.get("faqs")
    if faqs and isinstance(faqs, list):
        metafields.append(mf("custom", "faqs", "json", build_faqs(faqs)))

    # Single-line text fields
    for field in ["pack_size", "unit"]:
        val = enriched.get(field)
        if val:
            metafields.append(mf("custom", field, "single_line_text_field", str(val)))

    # Fix 4: Google Shopping category -- write to mm-google-shopping namespace
    # The explicit metafield overrides the channel auto-mapping when present.
    # Both the native category (set separately via productUpdate category field)
    # and this metafield are written to keep them consistent.
    cat_id = enriched.get("google_product_category_id")
    if cat_id:
        metafields.append(mf(
            "mm-google-shopping", "google_product_category",
            "string", str(cat_id)
        ))

    return metafields

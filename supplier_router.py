"""
supplier_router.py -- Routes each product to the correct supplier URL for scraping.

Key rules from the handoff:
- Prefix identifies the SOURCE (supplier/distributor), not the brand.
- MULTI-BRAND prefixes must route by the product's BRAND field, not the prefix domain.
- IGNORE prefixes skip scraping entirely and go straight to Shopify-only enrichment.
- ACCO prefixes (AA, PQ, CU) are login-gated -- currently fallback to Shopify-only.
"""

import csv
from pathlib import Path
from typing import Optional

# Load supplier map at module level
_SUPPLIER_MAP: dict[str, dict] = {}
_BRAND_TO_URL: dict[str, str] = {}

# Known brand-to-URL mappings for MULTI-BRAND distributor routing
# These supplement what can be inferred from the supplier map
_BRAND_URL_OVERRIDES: dict[str, str] = {
    # DS distributor brands
    "canon": "https://www.canon.com.au",
    "brother": "https://www.brother.com.au/en",
    "lindy": "https://www.lindy.com",
    "post it": "https://www.post-it.com/3M/en_US/post-it",
    "post-it": "https://www.post-it.com/3M/en_US/post-it",
    # GN wholesaler brands
    "collins debden": "https://www.collinsdebden.com.au",
    "collins": "https://www.collinsdebden.com.au",
    "double a": "https://www.doubleapaper.com.au",
    "rainbow": "https://www.collinsdebden.com.au",
    "micador": "https://www.micador.com.au",
    # AM wholesaler brands
    "velcro brand": "https://www.velcro.com.au",
    "velcro": "https://www.velcro.com.au",
    "osmer": "https://www.osmer.com.au",
    "nikko": "https://www.nikko.com.au",
    # GS wholesaler brands
    "spencil": "https://spencil.com.au",
    # LA brands
    "twinings": "https://twinings.com.au",
    "lavazza": "https://www.lavazza.com.au",
    # CC brands
    "arnott's": "https://www.arnotts.com.au",
    "arnotts": "https://www.arnotts.com.au",
    "cadbury": "https://www.cadbury.com.au",
    # SD multi-brand (Sanford/Newell)
    "dymo": "https://www.dymo.com.au",
    "parker": "https://www.parkerpen.com",
    "waterman": "https://www.waterman.com",
    "papermate": "https://www.papermate.com",
    "inkjoy": "https://www.papermate.com",
    # 3M brands
    "scotch": "https://www.3m.com.au",
    "command": "https://www.commandbrand.com.au/3M/en_AU/command-au",
}

# MULTI-BRAND prefixes: these distributors carry many brands
# Route by vendor/brand field, not by prefix domain
_MULTI_BRAND_PREFIXES = {"DS", "GN", "AM", "GS", "LA", "CC"}

# IGNORE prefixes: no supplier URL available, Shopify-only enrichment
_IGNORE_PREFIXES = {
    "MO", "AT", "BS", "MT", "ND", "PB", "AL", "SX", "#P",
    "V9", "GB", "13", "S0", "FF", "SE", "21", "ME"
}

# ACCO prefixes: login-gated portal -- fallback to Shopify-only until creds confirmed
_ACCO_PREFIXES = {"AA", "PQ", "CU"}


def load_supplier_map(csv_path: str):
    """Load the prefix_supplier_map_FINAL.csv into memory."""
    global _SUPPLIER_MAP
    if not Path(csv_path).exists():
        print(f"[router] Supplier map not found: {csv_path} -- using defaults")
        return
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            prefix = str(row.get("supplier_prefix", "")).strip()
            if prefix:
                _SUPPLIER_MAP[prefix] = {
                    "supplier_name": row.get("supplier_name", ""),
                    "supplier_domain": row.get("supplier_domain", ""),
                    "scrape_strategy": row.get("scrape_strategy", ""),
                    "status": row.get("status", ""),
                    "top_brands": row.get("top_brands", ""),
                    "product_count": row.get("product_count", 0),
                }
    print(f"[router] Loaded {len(_SUPPLIER_MAP)} supplier prefix mappings.")


def resolve_scrape_url(sku: str, brand: str) -> tuple[Optional[str], str]:
    """
    Resolve the supplier URL to scrape for a given product.

    Returns:
        (url, strategy) where strategy is one of:
            "single_brand"   -- direct hit on known single-brand site
            "multi_brand"    -- routed by brand field
            "acco_gated"     -- ACCO portal (login required, currently fallback)
            "ignore"         -- no supplier data available
            "unknown"        -- prefix not in map
    """
    prefix = sku[:2].upper()
    brand_lower = brand.lower().strip()

    # Hard IGNORE
    if prefix in _IGNORE_PREFIXES:
        return None, "ignore"

    # ACCO gated -- no creds yet
    if prefix in _ACCO_PREFIXES:
        return None, "acco_gated"

    # MULTI-BRAND: route by brand field
    if prefix in _MULTI_BRAND_PREFIXES:
        url = _BRAND_URL_OVERRIDES.get(brand_lower)
        if url:
            return url, "multi_brand"
        # Try partial match
        for brand_key, brand_url in _BRAND_URL_OVERRIDES.items():
            if brand_key in brand_lower or brand_lower in brand_key:
                return brand_url, "multi_brand"
        # No brand match found -- fallback
        return None, "multi_brand_no_match"

    # Single brand -- use domain from map
    supplier = _SUPPLIER_MAP.get(prefix)
    if not supplier:
        return None, "unknown_prefix"

    domain = supplier.get("supplier_domain", "")
    # Clean up domain -- take first URL if multiple listed
    if domain:
        domain = domain.split("|")[0].split(";")[0].split(" or ")[0].strip()
        if domain and not domain.startswith("http"):
            domain = "https://" + domain
        return domain if domain else None, "single_brand"

    return None, "no_domain"


def get_supplier_info(sku: str) -> dict:
    """Return full supplier info dict for a given SKU prefix."""
    prefix = sku[:2].upper()
    return _SUPPLIER_MAP.get(prefix, {})

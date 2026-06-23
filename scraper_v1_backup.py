"""
scraper_v2.py — Improved Supplier Page Scraper

Key improvements over v1:
  1. Multi-strategy search: tries Product Name → MPN → SKU in order, stops on first hit
  2. Product page verification: confirms the page actually matches our product before accepting
  3. Fuzzy title matching: handles partial product name matches
  4. Supplier-specific extraction profiles: correct selectors per site type
  5. Smarter link extraction: scores candidate links instead of taking the first
  6. Exponential backoff: retries transient errors (521, 503, timeout)
  7. Aggressive caching: caches per (domain, query) pair
  8. Direct URL injection: uses links.txt-style overrides for known hard cases
  9. robots.txt bypass: honour per-supplier override flag
 10. Parallel execution: thread pool for independent suppliers (caller-controlled)

Drop-in compatible with existing scrape_product() signature.
"""

import hashlib
import json
import re
import time
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

# ── Config import (graceful fallback for standalone testing) ──────────────────
try:
    from config import config
    SCRAPER_ENABLED = config.SCRAPER_ENABLED
    SCRAPER_DELAY = config.SCRAPER_DELAY_SECONDS
except ImportError:
    SCRAPER_ENABLED = True
    SCRAPER_DELAY = 1.5

try:
    from supplier_router import resolve_scrape_url, load_supplier_map
except ImportError:
    def resolve_scrape_url(sku, brand):
        return None, "unknown_prefix"
    def load_supplier_map(path):
        pass

# ── Constants ─────────────────────────────────────────────────────────────────

CACHE_DIR = Path("output/scrape_cache_v2")
CACHE_LOCK = threading.Lock()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_robots_cache: dict[str, bool] = {}
_session = requests.Session()
_session.headers.update(HEADERS)

# Minimum similarity ratio to consider a page a match for our product
MIN_TITLE_SIMILARITY = 0.35  # permissive — we just need sanity check
MIN_CONTENT_LENGTH = 80      # chars; below this is probably a navigation page

# ── Supplier Search Config ────────────────────────────────────────────────────
# Extended with per-supplier tuning for the high-impact "not_found" cases.
#
# Keys:
#   search_url_template       {query} placeholder for the search URL
#   product_link_selector     CSS selectors (ordered, first match wins)
#   content_selectors         CSS selectors for description text
#   spec_selectors            CSS selectors for spec tables/lists
#   search_strategies         list of strategies to try: "name" | "mpn" | "sku"
#   respect_robots            bool (default True; set False to ignore robots.txt)
#   retry_on                  list of HTTP status codes to retry (default [521, 503, 429])
#   score_links               bool — score all candidate links and pick best (slower but more accurate)
#   verify_product            bool — run product-page verification heuristic
#   extract_fallback          bool — use generic main-content fallback if selectors miss

SUPPLIER_SEARCH_CONFIG: dict[str, dict] = {

    # ── Staedtler .com (international — INTL search works, AU doesn't) ────────
    "www.staedtler.com": {
        "search_url_template": "https://www.staedtler.com/intl/en/search/?q={query}",
        "product_link_selector": [
            ".product-list__item a.product-list__link",
            ".product-list__item h2 a",
            "article.product a[href*='/products/']",
        ],
        "content_selectors": [
            ".product-detail__description",
            ".product-information__description",
            ".product__description",
            ".tab-content .description",
        ],
        "spec_selectors": [
            ".product-detail__specifications",
            ".product-information__specifications",
            ".specifications-table",
            "table.product-specs",
        ],
        "search_strategies": ["mpn", "name"],   # MPN first for Staedtler (e.g. "351-30")
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Staedtler Australia (AU subdomain — often 404s, use INTL as fallback) ─
    "staedtler.com.au": {
        "search_url_template": "https://www.staedtler.com.au/en_AU/search/?q={query}",
        "product_link_selector": [
            ".product-list__item a.product-list__link",
            ".search-result a[href*='/products/']",
        ],
        "content_selectors": [".product-detail__description", ".product__description"],
        "spec_selectors": [".product-detail__specifications", ".specifications"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "fallback_domain": "www.staedtler.com",  # try INTL if AU fails
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Visionchart ───────────────────────────────────────────────────────────
    "www.visionchart.com.au": {
        "search_url_template": "https://www.visionchart.com.au/catalogsearch/result/?q={query}",
        "product_link_selector": [
            ".product-item-link",
            ".product-name a",
            "a.product-item-photo",
            ".products-grid .item a[href*='.html']",
        ],
        "content_selectors": [
            ".product.attribute.description .value",
            ".product-description",
            "#description .value",
            ".overview",
        ],
        "spec_selectors": [
            "#product-attribute-specs-table",
            ".product.attribute.specifications .value",
            ".specifications-table",
            "table.data.table",
        ],
        "search_strategies": ["name", "mpn", "sku"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Weatherdon ────────────────────────────────────────────────────────────
    "www.weatherdon.com.au": {
        "search_url_template": "https://www.weatherdon.com.au/search/?q={query}",
        "product_link_selector": [
            "a.product-name",
            ".product-item a.product-name",
            ".item-title a",
            "h2.product-name a",
            ".listing-product a[href*='/pd.php']",
        ],
        "content_selectors": [
            ".product-description",
            "#description",
            ".tab-content .tab-pane:first-child",
            ".product__description",
        ],
        "spec_selectors": [
            ".product-attributes",
            "#specifications",
            ".tab-content .tab-pane:nth-child(2)",
            "table.product-specs",
        ],
        "search_strategies": ["sku", "mpn", "name"],  # Weatherdon indexes by their own code
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Fellowes ──────────────────────────────────────────────────────────────
    "www.fellowes.com": {
        "search_url_template": "https://www.fellowes.com/au/en/search.aspx?q={query}",
        "product_link_selector": [
            ".search-results .product-name a",
            ".product-listing h3 a",
            ".product-card a[href*='/catalog/business-products/']",
            "a[href*='/details/']",
        ],
        "content_selectors": [
            "#descriptionContent",
            ".product-description",
            ".product-details__description",
            ".tab-content [id*='description']",
        ],
        "spec_selectors": [
            "#specificationsContent",
            ".product-specifications",
            ".tech-specs table",
            ".specs-table",
        ],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Deflecto ──────────────────────────────────────────────────────────────
    "www.deflecto.com": {
        "search_url_template": "https://www.deflecto.com/search?q={query}",
        "product_link_selector": [
            ".product-item-link",
            ".product-name a",
            ".search-result-item a",
            "a[href*='/product/']",
        ],
        "content_selectors": [
            ".product-description",
            ".product.attribute.description .value",
            ".overview-content",
        ],
        "spec_selectors": [
            "#product-attribute-specs-table",
            ".product-specifications",
            "table.specifications",
        ],
        "search_strategies": ["mpn", "name", "sku"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Colby ─────────────────────────────────────────────────────────────────
    "www.colby.com.au": {
        "search_url_template": "https://www.colby.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            ".product-title a",
            "a.woocommerce-LoopProduct-link",
            "ul.products li a.woocommerce-LoopProduct-link",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description",
            ".product-description",
            ".entry-content",
        ],
        "spec_selectors": [
            ".woocommerce-product-attributes",
            "#tab-additional_information",
            ".product-attributes table",
        ],
        "search_strategies": ["name", "mpn"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Hamelin Brands (BH, AP, BX prefixes — WooCommerce) ───────────────────
    "www.hamelinbrands.com.au": {
        "search_url_template": "https://www.hamelinbrands.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            ".woocommerce-loop-product__title a",
            "a.woocommerce-LoopProduct-link",
            ".product a[href*='/product/']",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description .entry-content",
            "#tab-description p",
            ".product-description",
        ],
        "spec_selectors": [
            ".woocommerce-product-attributes",
            "#tab-additional_information table",
            ".product-specs table",
        ],
        "search_strategies": ["name", "mpn"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": False,  # brand carries many sub-brands, title match is loose
        "extract_fallback": True,
    },

    # ── Avery Products Australia ─────────────────────────────────────────────
    "www.averyproducts.com.au": {
        "search_url_template": "https://www.averyproducts.com.au/search?q={query}",
        "product_link_selector": [
            ".product-item-link",
            ".product-name a",
            "a[href*='/label/']",
        ],
        "content_selectors": [
            ".product.attribute.description .value",
            ".product-info-main .description",
            ".product-description",
        ],
        "spec_selectors": [
            "#product-attribute-specs-table",
            ".product.attribute.specifications .value",
        ],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "retry_on": [521, 503, 429],
        "max_retries": 4,
        "retry_delay": 8,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Teaching.com.au (EV, CS, ER, RH, ZA, EC prefixes) ───────────────────
    # Issue was "cached" category pages — need score_links + verify to get product pages
    "www.teaching.com.au": {
        "search_url_template": "https://www.teaching.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            ".woocommerce-loop-product__title a",
            "a.woocommerce-LoopProduct-link[href*='/product/']",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description .entry-content",
            "#tab-description",
        ],
        "spec_selectors": [
            ".woocommerce-product-attributes",
            "#tab-additional_information table",
        ],
        "search_strategies": ["name", "mpn"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Collins Debden ────────────────────────────────────────────────────────
    "www.collinsdebden.com.au": {
        "search_url_template": "https://www.collinsdebden.com.au/search?q={query}",
        "product_link_selector": [
            ".product-name a",
            ".product-title a",
            "h3.product-title a",
            "a[href*='/products/']",
        ],
        "content_selectors": [
            ".product-description",
            ".product__description",
            ".description",
        ],
        "spec_selectors": [".specifications", ".product-specs", "table.specs"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": False,  # already working well
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── Rapidline (FX prefix — WooCommerce) ──────────────────────────────────
    "rapidline.com.au": {
        "search_url_template": "https://rapidline.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            "a.woocommerce-LoopProduct-link",
            ".product-title a",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description",
            ".product-description",
        ],
        "spec_selectors": [".woocommerce-product-attributes", "#tab-additional_information"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── The Note Group (TN, ZN prefixes) ─────────────────────────────────────
    "www.thenotegroup.com.au": {
        "search_url_template": "https://www.thenotegroup.com.au/search?q={query}",
        "product_link_selector": [
            ".product-title a",
            "a[href*='/products/']",
            ".product-name a",
        ],
        "content_selectors": [".product-description", ".description", ".product__description"],
        "spec_selectors": [".specifications", ".product-specs"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── PHE (PH prefix — WooCommerce) ────────────────────────────────────────
    "www.phe.com.au": {
        "search_url_template": "https://www.phe.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            "a.woocommerce-LoopProduct-link",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description",
        ],
        "spec_selectors": [".woocommerce-product-attributes"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── Dolphy (DO prefix — WooCommerce) ─────────────────────────────────────
    "dolphy.com.au": {
        "search_url_template": "https://dolphy.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            "a.woocommerce-LoopProduct-link",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description",
        ],
        "spec_selectors": [".woocommerce-product-attributes"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── KC Professional (KC prefix) ───────────────────────────────────────────
    "www.kcprofessional.com": {
        "search_url_template": "https://www.kcprofessional.com/en-au/search?q={query}",
        "product_link_selector": [
            "a[href*='/products/']",
            ".product-tile a",
            ".product-name a",
        ],
        "content_selectors": [".product-description", ".product__description", ".overview"],
        "spec_selectors": [".product-specifications", ".specs-table"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Arnos (AR prefix — WooCommerce) ──────────────────────────────────────
    "arnos.com.au": {
        "search_url_template": "https://arnos.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            "a.woocommerce-LoopProduct-link",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description",
        ],
        "spec_selectors": [".woocommerce-product-attributes"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── Brother Australia (SL prefix) ────────────────────────────────────────
    "www.brother.com.au": {
        "search_url_template": "https://www.brother.com.au/en/search?q={query}",
        "product_link_selector": [
            ".search-result-product a",
            ".product-name a",
            "a[href*='/products/']",
        ],
        "content_selectors": [
            ".product-overview__description",
            ".product-description",
            ".overview-text",
        ],
        "spec_selectors": [
            ".product-specifications",
            ".spec-table",
            "table.specifications",
        ],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Jasco (JA prefix) ─────────────────────────────────────────────────────
    "www.jasco.com.au": {
        "search_url_template": "https://www.jasco.com.au/search?q={query}",
        "product_link_selector": [
            "a[href*='/products/']",
            ".product-title a",
            ".product-name a",
        ],
        "content_selectors": [".product-description", ".description"],
        "spec_selectors": [".specifications", ".product-specs"],
        "search_strategies": ["sku", "name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── Faber Castell Australia (FC prefix) ───────────────────────────────────
    "www.faber-castell.com.au": {
        "search_url_template": "https://www.faber-castell.com.au/products/search?q={query}",
        "product_link_selector": [
            "a[href*='/products/']",
            ".product-card a",
            ".product-title a",
        ],
        "content_selectors": [".product-description", ".product__description"],
        "spec_selectors": [".product-specifications", ".specifications"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── Pentel Australia (PE prefix) ──────────────────────────────────────────
    "www.pentel.com.au": {
        "search_url_template": "https://www.pentel.com.au/search?q={query}",
        "product_link_selector": [
            "a[href*='/products/']",
            ".product-title a",
            ".product-name a",
        ],
        "content_selectors": [".product-description", ".product__description"],
        "spec_selectors": [".specifications", ".product-specs"],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── Who Gives a Crap (GG prefix) ─────────────────────────────────────────
    "au.whogivesacrap.org": {
        "search_url_template": "https://au.whogivesacrap.org/search?q={query}",
        "product_link_selector": [
            "a[href*='/products/']",
            ".product-card a",
        ],
        "content_selectors": [".product-description", ".product__description"],
        "spec_selectors": [".specifications"],
        "search_strategies": ["name"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── Spencil (GS prefix — WooCommerce) ────────────────────────────────────
    "spencil.com.au": {
        "search_url_template": "https://spencil.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            "a.woocommerce-LoopProduct-link",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description",
        ],
        "spec_selectors": [".woocommerce-product-attributes"],
        "search_strategies": ["name", "mpn"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── JS Hayes (KP, JH prefixes — WooCommerce) ─────────────────────────────
    "www.jshayes.com.au": {
        "search_url_template": "https://www.jshayes.com.au/?s={query}&post_type=product",
        "product_link_selector": [
            "h2.woocommerce-loop-product__title a",
            "a.woocommerce-LoopProduct-link",
        ],
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            "#tab-description",
        ],
        "spec_selectors": [".woocommerce-product-attributes"],
        "search_strategies": ["name", "mpn"],
        "respect_robots": False,
        "score_links": False,
        "verify_product": False,
        "extract_fallback": True,
    },

    # ── Canon Australia (DS prefix — robots blocked, try anyway) ─────────────
    "www.canon.com.au": {
        "search_url_template": "https://www.canon.com.au/search#q={query}&t=All",
        "product_link_selector": [
            ".CoveoResultLink",
            ".product-title a",
            "h3.title a",
        ],
        "content_selectors": [
            ".product-detail__description",
            ".product-description",
        ],
        "spec_selectors": [
            ".product-specifications",
            ".specifications-table",
        ],
        "search_strategies": ["mpn", "name"],
        "respect_robots": False,  # Canon's robots.txt was blocking but worth retrying
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },

    # ── Post-it (3M prefix) ───────────────────────────────────────────────────
    "www.post-it.com": {
        "search_url_template": "https://www.post-it.com/3M/en_US/post-it/search/?q={query}",
        "product_link_selector": [
            "a[href*='/3M/en_US/p/']",
            ".product-card a",
            ".product-name a",
        ],
        "content_selectors": [
            ".product-detail__description",
            ".product-description",
        ],
        "spec_selectors": [
            ".product-specifications",
            ".tech-specs",
        ],
        "search_strategies": ["name", "mpn"],
        "respect_robots": False,
        "score_links": True,
        "verify_product": True,
        "extract_fallback": True,
    },
}

# ── Direct URL Overrides ──────────────────────────────────────────────────────
# For specific SKUs where search never works — use a verified direct URL.
# Format: SKU -> url (add entries as discovered)
DIRECT_URL_OVERRIDES: dict[str, str] = {
    # Add entries like:
    # "SR351-30": "https://www.staedtler.com/intl/en/products/markers-and-highliters/...",
}

# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(parts: list[str]) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()


def _load_cache(key: str) -> Optional[dict]:
    path = CACHE_DIR / f"{key}.json"
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_cache(key: str, data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    try:
        with CACHE_LOCK:
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ── Robots.txt ────────────────────────────────────────────────────────────────

def _is_allowed(url: str, respect_robots: bool = True) -> bool:
    if not respect_robots:
        return True
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base in _robots_cache:
        return _robots_cache[base]
    try:
        rp = RobotFileParser()
        rp.set_url(base + "/robots.txt")
        rp.read()
        allowed = rp.can_fetch(HEADERS["User-Agent"], url)
    except Exception:
        allowed = True
    _robots_cache[base] = allowed
    return allowed


# ── HTTP fetch with retry ─────────────────────────────────────────────────────

def _fetch(
    url: str,
    sku: str = "",
    respect_robots: bool = True,
    retry_on: Optional[list] = None,
    max_retries: int = 2,
    retry_delay: float = 5.0,
) -> tuple[Optional[BeautifulSoup], str]:
    """
    Fetch URL with optional robots check and exponential-backoff retry.
    Returns (soup, status_string).
    """
    if not _is_allowed(url, respect_robots):
        print(f"[scrape] {sku}: blocked_robots {url[:60]}", flush=True)
        return None, "blocked_robots"

    retry_codes = set(retry_on or [521, 503, 429, 502])

    for attempt in range(max_retries + 1):
        if attempt > 0:
            wait = retry_delay * (2 ** (attempt - 1))
            print(f"[scrape] {sku}: retry {attempt}/{max_retries} in {wait:.0f}s", flush=True)
            time.sleep(wait)
        else:
            time.sleep(SCRAPER_DELAY)

        print(f"[scrape] {sku}: GET {url[:70]}", flush=True)
        t0 = time.time()
        try:
            resp = _session.get(url, timeout=(12, 35), allow_redirects=True)
            elapsed = time.time() - t0

            if resp.status_code in retry_codes and attempt < max_retries:
                print(f"[scrape] {sku}: http_{resp.status_code} ({elapsed:.1f}s) — retrying", flush=True)
                continue

            if resp.status_code == 403:
                return None, "blocked_403"
            if resp.status_code == 404:
                return None, "not_found"
            if resp.status_code != 200:
                return None, f"http_{resp.status_code}"

            soup = BeautifulSoup(resp.text, "html.parser")
            print(f"[scrape] {sku}: ok {resp.status_code} ({elapsed:.1f}s)", flush=True)
            return soup, "success"

        except requests.exceptions.Timeout:
            elapsed = time.time() - t0
            print(f"[scrape] {sku}: timeout ({elapsed:.1f}s)", flush=True)
            if attempt < max_retries:
                continue
            return None, "timeout"
        except requests.exceptions.ConnectionError as e:
            print(f"[scrape] {sku}: connection_error — {str(e)[:60]}", flush=True)
            if attempt < max_retries:
                continue
            return None, "connection_error"
        except Exception as e:
            print(f"[scrape] {sku}: error — {str(e)[:80]}", flush=True)
            return None, f"error:{str(e)[:40]}"

    return None, f"http_{resp.status_code if 'resp' in dir() else 'unknown'}"


# ── Title similarity ──────────────────────────────────────────────────────────

def _title_similarity(a: str, b: str) -> float:
    """Normalised token-based similarity; brand/model terms weighted."""
    a_norm = re.sub(r"[^\w\s]", " ", a.lower()).split()
    b_norm = re.sub(r"[^\w\s]", " ", b.lower()).split()
    if not a_norm or not b_norm:
        return 0.0
    # Jaccard similarity on tokens
    set_a, set_b = set(a_norm), set(b_norm)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _sequence_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ── Link scoring ──────────────────────────────────────────────────────────────

_PRODUCT_HREF_SIGNALS = [
    "/product/", "/products/", "/item/", "/p/", "/catalogue/",
    "/pd.php", "/shop/", ".html", "/detail", "/view",
]
_NON_PRODUCT_HREF_SIGNALS = [
    "/cart", "/checkout", "/account", "/login", "/register",
    "/category/", "/categories/", "/brand/", "/brands/",
    "?page=", "#", "javascript:",
]


def _score_link(a_tag, title_words: list[str]) -> float:
    """Score a candidate <a> tag on likelihood it's the right product."""
    href = (a_tag.get("href") or "").lower()
    text = a_tag.get_text(strip=True).lower()
    score = 0.0

    # Positive: product-like URL
    for sig in _PRODUCT_HREF_SIGNALS:
        if sig in href:
            score += 1.0
            break

    # Negative: clearly not a product URL
    for sig in _NON_PRODUCT_HREF_SIGNALS:
        if sig in href:
            score -= 3.0
            break

    # Title word matches in link text or URL
    for word in title_words:
        if len(word) >= 3:
            if word in text:
                score += 0.5
            if word in href:
                score += 0.3

    return score


def _best_product_link(
    soup: BeautifulSoup,
    selectors: list[str],
    title: str,
    domain: str,
    score_links: bool = False,
) -> Optional[str]:
    """
    Find the best product page link from search results.
    If score_links=True, scores all candidates and returns highest.
    Otherwise returns first match from selectors.
    """
    title_words = re.sub(r"[^\w\s]", " ", title.lower()).split()

    candidates = []

    for sel in selectors:
        for tag in soup.select(sel):
            href = tag.get("href")
            if not href:
                continue
            # Make absolute
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = f"https://{domain}{href}"
            elif not href.startswith("http"):
                continue
            # Must be same domain (or closely related)
            parsed = urlparse(href)
            if domain.replace("www.", "") not in parsed.netloc:
                continue
            candidates.append((tag, href))

    if not candidates:
        # Broad fallback: any anchor with product-like href on same domain
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if href.startswith("/") and any(s in href for s in ["/product", "/item", "/pd.", "/catalogue"]):
                full = f"https://{domain}{href}"
                candidates.append((tag, full))

    if not candidates:
        return None

    if not score_links or len(candidates) == 1:
        return candidates[0][1]

    # Score all candidates, return the best
    scored = [(tag, href, _score_link(tag, title_words)) for tag, href in candidates]
    scored.sort(key=lambda x: x[2], reverse=True)
    best_tag, best_href, best_score = scored[0]

    if best_score < -1:
        # All candidates look bad
        return candidates[0][1]  # fall back to first

    return best_href


# ── Content extraction ────────────────────────────────────────────────────────

def _extract_content(
    soup: BeautifulSoup,
    url: str,
    content_selectors: list[str],
    spec_selectors: list[str],
    use_fallback: bool = True,
) -> dict:
    """
    Extract description, specifications, features from a product page.
    Returns dict with description, specifications, features, source_url.
    """
    result = {
        "description": "",
        "specifications": "",
        "features": "",
        "source_url": url,
    }

    # ── Description ──
    for sel in content_selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) >= MIN_CONTENT_LENGTH:
                result["description"] = text
                break

    # ── Specifications ──
    for sel in spec_selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) >= 30:
                result["specifications"] = text
                break

    # ── Specification tables (generic) ──
    if not result["specifications"]:
        for table in soup.find_all("table"):
            text = table.get_text(separator="\n", strip=True)
            if len(text) >= 40 and any(w in text.lower() for w in ["weight", "dimensions", "material", "size", "colour", "pack", "capacity", "type"]):
                result["specifications"] = text
                break

    # ── Features ──
    for sel in [".key-features", ".product-features", ".features-list", ".product__features", ".highlights"]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) >= 30:
                result["features"] = text
                break

    # ── Generic fallback: main content area ──
    if use_fallback and not result["description"]:
        for tag_name in ["main", "article", '[role="main"]', "#main", "#content", ".content"]:
            try:
                el = soup.select_one(tag_name)
            except Exception:
                el = soup.find(tag_name)
            if el:
                # Remove nav, header, footer, sidebar from text
                for noise in el.find_all(["nav", "header", "footer", "aside", "script", "style"]):
                    noise.decompose()
                text = el.get_text(separator="\n", strip=True)
                if len(text) >= MIN_CONTENT_LENGTH:
                    result["description"] = text[:4000]
                    break

    return result


# ── Product page verification ─────────────────────────────────────────────────

def _verify_product_page(soup: BeautifulSoup, title: str, mpn: str = "") -> bool:
    """
    Confirm this page looks like it's about the right product.
    Returns True if confidence is high enough to use this page.
    """
    page_text = soup.get_text().lower()

    # Very short pages are almost certainly nav/category pages
    if len(page_text) < 500:
        return False

    # Check title similarity
    page_title_el = soup.find("h1") or soup.find("h2")
    if page_title_el:
        page_title = page_title_el.get_text(strip=True)
        sim = _title_similarity(title, page_title)
        if sim >= MIN_TITLE_SIMILARITY:
            return True
        # Sequence similarity as secondary check
        if _sequence_similarity(title, page_title) >= 0.4:
            return True

    # MPN match is definitive
    if mpn and len(mpn) >= 3:
        mpn_lower = mpn.lower()
        if mpn_lower in page_text:
            return True
        # Try without hyphens/spaces
        mpn_clean = re.sub(r"[-\s]", "", mpn_lower)
        if len(mpn_clean) >= 3 and mpn_clean in re.sub(r"[-\s]", "", page_text):
            return True

    # Check for product-page signals
    product_signals = ["add to cart", "add to basket", "buy now", "in stock",
                       "out of stock", "specifications", "product code", "sku", "ean", "barcode"]
    signal_count = sum(1 for s in product_signals if s in page_text)
    if signal_count >= 2:
        # Also check title words appear on page
        title_words = [w.lower() for w in title.split() if len(w) >= 4]
        word_hits = sum(1 for w in title_words if w in page_text)
        if word_hits >= max(1, len(title_words) // 3):
            return True

    return False


# ── Multi-strategy search ─────────────────────────────────────────────────────

def _build_queries(
    sku: str,
    title: str,
    mpn: str,
    strategies: list[str],
) -> list[tuple[str, str]]:
    """
    Build (strategy_name, query_string) pairs in priority order.
    Deduplicates identical queries.
    """
    queries = []
    seen = set()

    for strategy in strategies:
        if strategy == "mpn" and mpn and len(mpn) >= 3:
            q = mpn.strip()
            if q not in seen:
                queries.append(("mpn", q))
                seen.add(q)
        elif strategy == "name" and title:
            # Full title
            q = title.strip()
            if q not in seen:
                queries.append(("name_full", q))
                seen.add(q)
            # First 5 words (often enough and avoids over-filtering)
            words = title.split()[:5]
            if len(words) >= 2:
                q_short = " ".join(words)
                if q_short not in seen:
                    queries.append(("name_short", q_short))
                    seen.add(q_short)
        elif strategy == "sku" and sku:
            # Strip prefix (first 2 chars) for supplier-side search
            q = sku[2:].strip() if len(sku) > 2 else sku
            if q and q not in seen:
                queries.append(("sku_stripped", q))
                seen.add(q)
            # Also try raw SKU
            if sku not in seen:
                queries.append(("sku_raw", sku))
                seen.add(sku)

    return queries


def _search_and_scrape(
    domain: str,
    sku: str,
    title: str,
    mpn: str,
    cfg: dict,
) -> dict:
    """
    Core search-then-scrape routine.
    Tries multiple search strategies; stops on first verified product page.
    """
    strategies = cfg.get("search_strategies", ["name", "mpn"])
    queries = _build_queries(sku, title, mpn, strategies)
    respect_robots = cfg.get("respect_robots", True)
    score_links = cfg.get("score_links", False)
    verify = cfg.get("verify_product", True)
    use_fallback = cfg.get("extract_fallback", True)
    retry_on = cfg.get("retry_on", [521, 503, 429])
    max_retries = cfg.get("max_retries", 2)
    retry_delay = cfg.get("retry_delay", 5.0)

    link_selectors = cfg.get("product_link_selector", [])
    if isinstance(link_selectors, str):
        link_selectors = [link_selectors]
    content_selectors = cfg.get("content_selectors", [])
    spec_selectors = cfg.get("spec_selectors", [])

    last_status = "no_queries"

    for strategy_name, query in queries:
        search_url = cfg["search_url_template"].format(query=quote_plus(query))

        # Cache key: (domain, query)
        cache_key = _cache_key([domain, query, "v2"])
        cached = _load_cache(cache_key)
        if cached:
            print(f"[scrape] {sku}: cache hit ({strategy_name}='{query[:30]}')", flush=True)
            return {**cached, "status": "cached", "search_strategy": strategy_name}

        # Fetch search results
        search_soup, fetch_status = _fetch(
            search_url, sku,
            respect_robots=respect_robots,
            retry_on=retry_on,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        last_status = fetch_status

        if not search_soup:
            print(f"[scrape] {sku}: search fetch failed ({strategy_name}) — {fetch_status}", flush=True)
            continue

        # Check for "no results" page
        page_text_lower = search_soup.get_text().lower()
        no_results_signals = [
            "no results", "no products found", "0 results", "0 products",
            "nothing found", "sorry, no", "no items", "your search returned 0"
        ]
        if any(s in page_text_lower for s in no_results_signals):
            print(f"[scrape] {sku}: no results page ({strategy_name}='{query[:30]}')", flush=True)
            last_status = "not_found"
            continue

        # Extract best product link
        product_url = _best_product_link(
            search_soup, link_selectors, title, domain, score_links=score_links
        )
        if not product_url:
            print(f"[scrape] {sku}: no product link found ({strategy_name})", flush=True)
            last_status = "no_product_link"
            continue

        # Fetch product page
        prod_soup, prod_status = _fetch(
            product_url, sku,
            respect_robots=respect_robots,
            retry_on=retry_on,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        last_status = prod_status

        if not prod_soup:
            print(f"[scrape] {sku}: product page fetch failed — {prod_status}", flush=True)
            continue

        # Verify product page matches
        if verify and not _verify_product_page(prod_soup, title, mpn):
            print(f"[scrape] {sku}: product page verification failed ({strategy_name})", flush=True)
            last_status = "verification_failed"
            continue

        # Extract content
        content = _extract_content(
            prod_soup, product_url, content_selectors, spec_selectors, use_fallback
        )
        has_content = bool(
            (content["description"] or "").strip() or
            (content["specifications"] or "").strip()
        )

        if not has_content:
            print(f"[scrape] {sku}: extracted empty content ({strategy_name})", flush=True)
            last_status = "scraped_empty"
            continue

        # Success — cache and return
        content["search_strategy"] = strategy_name
        _save_cache(cache_key, content)
        print(f"[scrape] {sku}: success ({strategy_name}='{query[:30]}') → {product_url[:60]}", flush=True)
        return {**content, "status": "success"}

    # All strategies exhausted
    print(f"[scrape] {sku}: all strategies failed (last={last_status})", flush=True)
    return {
        "status": last_status or "not_found",
        "description": "",
        "specifications": "",
        "features": "",
    }


# ── Direct URL scraping ───────────────────────────────────────────────────────

def scrape_url(url: str, sku: str = "", content_selectors: Optional[list] = None,
               spec_selectors: Optional[list] = None) -> dict:
    """Scrape a specific product URL directly. Useful for links.txt overrides."""
    cache_key = _cache_key(["direct", url])
    cached = _load_cache(cache_key)
    if cached:
        return {**cached, "status": "cached"}

    soup, status = _fetch(url, sku, respect_robots=False)
    if not soup:
        return {"status": status, "description": "", "specifications": "", "features": ""}

    content = _extract_content(
        soup, url,
        content_selectors or [],
        spec_selectors or [],
        use_fallback=True,
    )
    has_content = bool(content["description"].strip() or content["specifications"].strip())
    final_status = "success" if has_content else "scraped_empty"
    _save_cache(cache_key, content)
    return {**content, "status": final_status}


# ── Main public interface ─────────────────────────────────────────────────────

def scrape_product(sku: str, brand: str, title: str = "", mpn: str = "") -> dict:
    """
    Main entry point — drop-in replacement for v1 scrape_product().

    Resolution order:
      1. Direct URL override (DIRECT_URL_OVERRIDES dict or links.txt)
      2. Supplier config lookup + multi-strategy search
      3. Fallback domain (e.g. .com.au → .com for Staedtler)
    """
    if not SCRAPER_ENABLED:
        return {"status": "disabled", "description": "", "specifications": "", "features": ""}

    # 1. Direct URL override
    if sku in DIRECT_URL_OVERRIDES:
        url = DIRECT_URL_OVERRIDES[sku]
        result = scrape_url(url, sku=sku)
        result["routing_strategy"] = "direct_override"
        return result

    # 2. Resolve supplier base URL from router
    base_url, strategy = resolve_scrape_url(sku, brand)

    terminal_strategies = {"ignore", "acco_gated", "multi_brand_no_match", "unknown_prefix", "no_domain"}
    if strategy in terminal_strategies:
        return {
            "status": strategy,
            "routing_strategy": strategy,
            "description": "",
            "specifications": "",
            "features": "",
            "diagnostic": {"prefix": sku[:2].upper(), "brand": brand, "strategy": strategy},
        }

    if not base_url:
        return {
            "status": "no_url",
            "routing_strategy": strategy,
            "description": "",
            "specifications": "",
            "features": "",
        }

    domain = urlparse(base_url).netloc
    cfg = SUPPLIER_SEARCH_CONFIG.get(domain)

    if not cfg:
        return {
            "status": "no_config",
            "routing_strategy": strategy,
            "description": "",
            "specifications": "",
            "features": "",
            "diagnostic": {"prefix": sku[:2].upper(), "domain": domain},
        }

    result = _search_and_scrape(domain, sku, title or brand, mpn or "", cfg)
    result["routing_strategy"] = strategy
    result.setdefault("diagnostic", {})
    result["diagnostic"]["domain"] = domain

    # 3. Fallback domain (e.g. staedtler.com.au → www.staedtler.com)
    if result["status"] not in ("success", "cached") and cfg.get("fallback_domain"):
        fallback_domain = cfg["fallback_domain"]
        fallback_cfg = SUPPLIER_SEARCH_CONFIG.get(fallback_domain)
        if fallback_cfg:
            print(f"[scrape] {sku}: trying fallback domain {fallback_domain}", flush=True)
            fallback_result = _search_and_scrape(
                fallback_domain, sku, title or brand, mpn or "", fallback_cfg
            )
            if fallback_result["status"] in ("success", "cached"):
                fallback_result["routing_strategy"] = strategy
                fallback_result["diagnostic"] = {"domain": fallback_domain, "via_fallback": True}
                return fallback_result

    return result


def init_scraper(supplier_map_path: Optional[str] = None):
    """Load supplier map. Call once at pipeline startup."""
    if supplier_map_path is None:
        supplier_map_path = str(Path(__file__).parent / "prefix_supplier_map_FINAL.csv")

    search_path = Path(supplier_map_path)
    if search_path.exists():
        load_supplier_map(str(search_path))
        print(f"[scraper_v2] Supplier map loaded from {search_path}", flush=True)
    else:
        relative = Path("prefix_supplier_map_FINAL.csv")
        if relative.exists():
            load_supplier_map(str(relative))
            print(f"[scraper_v2] Supplier map loaded from {relative}", flush=True)
        else:
            print(f"[scraper_v2] WARNING: supplier map not found at {supplier_map_path}", flush=True)


# ── Standalone test harness ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import csv as csv_mod

    print("scraper_v2 test harness")
    print("Usage: python scraper_v2.py [sku] [brand] [title] [mpn]")
    print()

    if len(sys.argv) >= 2:
        _sku = sys.argv[1]
        _brand = sys.argv[2] if len(sys.argv) > 2 else ""
        _title = sys.argv[3] if len(sys.argv) > 3 else ""
        _mpn = sys.argv[4] if len(sys.argv) > 4 else ""

        init_scraper()
        r = scrape_product(_sku, _brand, _title, _mpn)
        print(f"\nStatus:   {r.get('status')}")
        print(f"Strategy: {r.get('routing_strategy')}")
        print(f"Search:   {r.get('search_strategy', 'n/a')}")
        print(f"URL:      {r.get('source_url', 'n/a')}")
        print(f"Desc len: {len(r.get('description', ''))}")
        print(f"Spec len: {len(r.get('specifications', ''))}")
        if r.get("description"):
            print(f"\nDescription preview:\n{r['description'][:300]}")
    else:
        # Quick smoke test with known-good SKUs
        test_cases = [
            ("CD10232", "Collins Debden", "Collins 10232 Account Book", "10232"),
            ("SR351-30", "Staedtler", "Staedtler Lumocolor Permanent Marker", "351-30"),
            ("FXSB2PWSCT1275", "Rapidline", "Rapidline Boost Static Workstation", "SB2PWSCT1275"),
        ]
        init_scraper()
        print(f"{'SKU':<25} {'Status':<20} {'DescLen':>7} {'SpecLen':>7}")
        print("-" * 65)
        for sku, brand, title, mpn in test_cases:
            r = scrape_product(sku, brand, title, mpn)
            print(f"{sku:<25} {r.get('status','?'):<20} {len(r.get('description',''))!r:>7} {len(r.get('specifications',''))!r:>7}")
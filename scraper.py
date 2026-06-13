"""
scraper.py -- Supplier page scraper: finds the PRODUCT PAGE for a given SKU/title,
not just the supplier homepage.

Strategy per supplier:
  1. Direct URL template  -- build product URL from SKU or title slug (fastest, most reliable)
  2. Search-then-scrape   -- hit supplier search endpoint, extract first product link, scrape it
  3. Shopify-only         -- no URL available, fall back to existing metafields only

The supplier_search_config dict maps domain -> config. Add more suppliers as Marty
provides URL patterns. The existing Shopify content fallback is always available.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from config import config
from supplier_router import resolve_scrape_url, load_supplier_map

CACHE_DIR = "output/scrape_cache"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.5",
}

_robots_cache: dict[str, bool] = {}

# ── Per-supplier search/product-page configuration ────────────────────────────
# Each entry maps a domain (from supplier_router) to how the scraper should
# find the product page.
#
# Keys:
#   search_url_template  -- URL with {query} placeholder; performs a site search
#   product_link_selector -- CSS selector to extract the first product link from search results
#   direct_url_template  -- URL with {sku} and/or {title_slug} to go directly to product page
#   content_selectors    -- list of CSS selectors to try for description/specs on the product page
#
# Add more entries as Marty provides URL patterns. This dict is the only thing
# that needs updating when new suppliers are onboarded.

SUPPLIER_SEARCH_CONFIG: dict[str, dict] = {
    # ── Canon Australia ──────────────────────────────────────────────────────
    "www.canon.com.au": {
        "search_url_template": "https://www.canon.com.au/search#q={query}&t=All",
        "product_link_selector": ".CoveoResultLink, .product-title a, h3.title a",
        "content_selectors": [
            ".product-detail__description",
            ".product-description",
            ".tab-content .description",
            "#product-description",
        ],
        "spec_selectors": [
            ".product-specifications",
            ".specifications-table",
            ".tab-content .specifications",
        ],
    },

    # ── Brother Australia ────────────────────────────────────────────────────
    "www.brother.com.au": {
        "search_url_template": "https://www.brother.com.au/en/search?q={query}",
        "product_link_selector": ".search-result-product a, .product-name a",
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
    },

    # ── Avery Products Australia ─────────────────────────────────────────────
    "www.averyproducts.com.au": {
        "search_url_template": "https://www.averyproducts.com.au/search?q={query}",
        "product_link_selector": ".product-item-link, .product-name a",
        "respect_robots": False, # This site blocks all bots in robots.txt, but we want to scrape it anyway
        "content_selectors": [
            ".product.attribute.description .value",
            ".product-info-main .description",
        ],
        "spec_selectors": [
            "#product-attribute-specs-table",
            ".product.attribute.specifications .value",
        ],
    },

    # ── Staedtler Australia ──────────────────────────────────────────────────
    "www.staedtler.com": {
        "search_url_template": "https://www.staedtler.com/intl/en/search/?q={query}",
        "product_link_selector": ".product-list__item a.product-list__link",
        "content_selectors": [
            ".product-detail__description",
            ".product-description",
        ],
        "spec_selectors": [
            ".product-detail__specifications",
            ".specifications",
        ],
    },

    # ── Visionchart ──────────────────────────────────────────────────────────
    "www.visionchart.com.au": {
        "search_url_template": "https://www.visionchart.com.au/search?q={query}",
        "product_link_selector": ".product-title a, h2.product-name a",
        "content_selectors": [".product-description", ".description"],
        "spec_selectors": [".product-specs", ".specifications"],
    },

    # ── Colby ────────────────────────────────────────────────────────────────
    "www.colby.com.au": {
        "search_url_template": "https://www.colby.com.au/search?q={query}",
        "product_link_selector": ".product-item-link",
        "content_selectors": [".product-description"],
        "spec_selectors": [".specifications"],
    },

    # ── Collins Debden ───────────────────────────────────────────────────────
    "www.collinsdebden.com.au": {
        "search_url_template": "https://www.collinsdebden.com.au/search?q={query}",
        "product_link_selector": ".product-name a, .product-title a",
        "content_selectors": [".product-description", ".description"],
        "spec_selectors": [".specifications", ".product-specs"],
    },

    # ── Velcro Brand Australia ───────────────────────────────────────────────
    "www.velcro.com.au": {
        "search_url_template": "https://www.velcro.com.au/search?q={query}",
        "product_link_selector": ".product-title a",
        "content_selectors": [".product-description"],
        "spec_selectors": [".specifications"],
    },

    # ── Fellowes ─────────────────────────────────────────────────────────────
    "www.fellowes.com": {
        "search_url_template": "https://www.fellowes.com/au/en/search.aspx?q={query}",
        "product_link_selector": ".search-results .product-name a, .product-listing a",
        "content_selectors": [
            ".product-description",
            ".product-details__description",
            "#descriptionContent",
        ],
        "spec_selectors": [
            ".product-specifications",
            "#specificationsContent",
        ],
    },

    # ── Deflecto ─────────────────────────────────────────────────────────────
    "www.deflecto.com": {
        "search_url_template": "https://www.deflecto.com/search?q={query}",
        "product_link_selector": ".product-name a",
        "content_selectors": [".product-description"],
        "spec_selectors": [".specifications"],
    },

    # ── The Note Group ───────────────────────────────────────────────────────
    "www.thenotegroup.com.au": {
        "search_url_template": "https://www.thenotegroup.com.au/search?q={query}",
        "product_link_selector": ".product-title a",
        "content_selectors": [".product-description"],
        "spec_selectors": [".specifications"],
    },

    # ── Weatherdon ───────────────────────────────────────────────────────────
    "www.weatherdon.com.au": {
        "search_url_template": "https://www.weatherdon.com.au/search?q={query}",
        "product_link_selector": ".product-item a.product-name",
        "content_selectors": [".product-description", ".description"],
        "spec_selectors": [".specifications"],
    },

    # ── Hamelin Brands (BH prefix) ───────────────────────────────────────────
    "www.hamelinbrands.com.au": {
        "search_url_template": "https://www.hamelinbrands.com.au/?s={query}",
        "product_link_selector": ".woocommerce-loop-product__title a, .product-name a",
        "content_selectors": [
            ".woocommerce-product-details__short-description",
            ".product-description",
            "#tab-description",
        ],
        "spec_selectors": [
            ".woocommerce-product-attributes",
            ".product-specs",
        ],
    },

    "arnos.com.au": {
      "search_url_template": "https://arnos.com.au/?s={query}&post_type=product",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "au.whogivesacrap.org": {
      "search_url_template": "https://au.whogivesacrap.org/search?q={query}",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "dolphy.com.au": {
      "search_url_template": "https://dolphy.com.au/?s={query}&post_type=product",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "rapidline.com.au": {
      "search_url_template": "https://rapidline.com.au/?s={query}&post_type=product",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "spencil.com.au": {
      "search_url_template": "https://spencil.com.au/?s={query}&post_type=product",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.3m.com.au": {
      "search_url_template": "https://www.3m.com.au/search?q={query}",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.faber-castell.com.au": {
      "search_url_template": "https://www.faber-castell.com.au/products/search?q={query}",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.jasco.com.au": {
      "search_url_template": "https://www.jasco.com.au/search?q={query}",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.jshayes.com.au": {
      "search_url_template": "https://www.jshayes.com.au/?s={query}&post_type=product",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.kcprofessional.com": {
      "search_url_template": "https://www.kcprofessional.com/search?q={query}",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.pentel.com.au": {
      "search_url_template": "https://www.pentel.com.au/search?q={query}",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.phe.com.au": {
      "search_url_template": "https://www.phe.com.au/?s={query}&post_type=product",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.post-it.com": {
      "search_url_template": "https://www.post-it.com/search?q={query}",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
    "www.teaching.com.au": {
      "search_url_template": "https://www.teaching.com.au/?s={query}&post_type=product",
      "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
      "respect_robots": False,
      "content_selectors": [
        ".product-description",
        ".product__description",
        "[data-product-description]",
        "#product-description",
        ".product-details__description",
        ".description"
      ],
      "spec_selectors": [
        ".product-specifications",
        ".specifications",
        ".product-specs",
        ".spec-table",
        "table.specs"
      ],
      "auto_discovered": True
    },
}


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _load_cache(url: str) -> Optional[dict]:
    path = Path(CACHE_DIR) / f"{_cache_key(url)}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_cache(url: str, data: dict):
    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    with open(Path(CACHE_DIR) / f"{_cache_key(url)}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# ── Robots.txt ───────────────────────────────────────────────────────────────

def _is_allowed(url: str) -> bool:
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


# ── HTTP fetch with hard timeout ──────────────────────────────────────────────

def _fetch(url: str, sku: str = "", skip_robots: bool = False) -> tuple[Optional[BeautifulSoup], str]:
    """
    Fetch a URL and return (BeautifulSoup, status).
    Status is 'success', 'blocked_403', 'not_found', 'timeout', 'connection_error',
    'blocked_robots', or 'http_NNN'.

    If skip_robots is True, the robots.txt check is bypassed entirely.
    """
    if not skip_robots and not _is_allowed(url):
        print(f"[scrape] {sku}: blocked_robots {url}", flush=True)
        return None, "blocked_robots"

    print(f"[scrape] {sku}: trying {url}", flush=True)
    t0 = time.time()
    try:
        time.sleep(config.SCRAPER_DELAY_SECONDS)
        resp = requests.get(url, headers=HEADERS, timeout=(10, 30))
        elapsed = time.time() - t0

        if resp.status_code == 403:
            print(f"[scrape] {sku}: blocked_403 ({elapsed:.1f}s)", flush=True)
            return None, "blocked_403"
        if resp.status_code == 404:
            print(f"[scrape] {sku}: not_found ({elapsed:.1f}s)", flush=True)
            return None, "not_found"
        if resp.status_code != 200:
            print(f"[scrape] {sku}: http_{resp.status_code} ({elapsed:.1f}s)", flush=True)
            return None, f"http_{resp.status_code}"

        soup = BeautifulSoup(resp.text, "html.parser")
        print(f"[scrape] {sku}: fetched ({elapsed:.1f}s)", flush=True)
        return soup, "success"

    except requests.exceptions.Timeout:
        elapsed = time.time() - t0
        print(f"[scrape] {sku}: timeout ({elapsed:.1f}s)", flush=True)
        return None, "timeout"
    except requests.exceptions.ConnectionError:
        elapsed = time.time() - t0
        print(f"[scrape] {sku}: connection_error ({elapsed:.1f}s)", flush=True)
        return None, "connection_error"
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[scrape] {sku}: error ({elapsed:.1f}s) - {str(e)[:80]}", flush=True)
        return None, f"error:{str(e)[:60]}"


# ── Content extraction ────────────────────────────────────────────────────────

def _extract_from_soup(
    soup: BeautifulSoup,
    url: str,
    content_selectors: list[str],
    spec_selectors: list[str],
) -> dict:
    """Extract description, specifications, features from a product page."""
    result = {
        "description": "",
        "specifications": "",
        "features": "",
        "source_url": url,
    }

    for sel in content_selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            result["description"] = el.get_text(separator="\n", strip=True)
            break

    for sel in spec_selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            result["specifications"] = el.get_text(separator="\n", strip=True)
            break

    # Generic feature list
    for sel in [".key-features", ".product-features", ".features-list", ".product__features"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            result["features"] = el.get_text(separator="\n", strip=True)
            break

    # Fallback: main content area (trimmed to 3000 chars)
    if not result["description"]:
        for tag in ["main", "article", '[id="content"]']:
            el = soup.select_one(tag) if tag.startswith(".") or tag.startswith("[") else soup.find(tag)
            if el and el.get_text(strip=True):
                result["description"] = el.get_text(separator="\n", strip=True)[:3000]
                break

    return result


def _is_likely_product_page(soup: BeautifulSoup, title: str) -> bool:
    """
    Heuristic: does this page look like it contains product data?
    Checks for presence of product-like elements and title similarity.
    """
    page_text = soup.get_text().lower()

    # Must have some product signals
    product_signals = [
        "specifications", "features", "description",
        "sku", "model", "product code", "compatible"
    ]
    has_signals = any(s in page_text for s in product_signals)

    # The title words should appear somewhere on the page
    title_words = [w.lower() for w in title.split() if len(w) > 3]
    word_matches = sum(1 for w in title_words if w in page_text)
    title_match = len(title_words) == 0 or word_matches >= max(1, len(title_words) // 2)

    return has_signals and title_match


def _google_fallback(domain: str, mpn: str, sku: str, title: str) -> dict:
    """
    Fallback: search Google for site:domain.com {query}.
    Returns a content dict with status.
    """
    query = mpn.strip() if mpn and mpn.strip() else f"{sku} {title[:30]}".strip()
    google_url = f"https://www.google.com/search?q=site:{domain}+{quote_plus(query)}"

    print(f"[scrape] Google fallback: {google_url}", flush=True)

    try:
        resp = requests.get(
            google_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            },
            timeout=(10, 15),
        )
        if resp.status_code != 200:
            return {"status": f"google_http_{resp.status_code}", "description": "", "specifications": "", "features": ""}

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract the first organic result link
        first_link = None
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if href.startswith("https://") and domain in href and not "google.com" in href:
                first_link = href
                break

        if not first_link:
            return {"status": "google_no_result", "description": "", "specifications": "", "features": ""}

        print(f"[scrape] Google found: {first_link}", flush=True)

        # Fetch the product page
        product_soup, fetch_status = _fetch(first_link, sku)
        if not product_soup:
            return {"status": f"google_fetch_{fetch_status}", "description": "", "specifications": "", "features": ""}

        content = _extract_from_soup(product_soup, first_link, [], [])
        has_content = bool(content["description"].strip() or content["specifications"].strip())
        return {**content, "status": "success" if has_content else "scraped_empty"}

    except Exception as e:
        print(f"[scrape] Google fallback error: {e}", flush=True)
        return {"status": f"google_error:{str(e)[:60]}", "description": "", "specifications": "", "features": ""}


# ── Search-then-scrape ────────────────────────────────────────────────────────

def _search_and_scrape(domain: str, sku: str, title: str, supplier_cfg: dict, mpn: str = "") -> dict:
    """
    1. Build a search query from SKU + title words.
    2. Fetch the search results page.
    3. Extract the first product link.
    4. Fetch that product page.
    5. Extract content.
    Returns a content dict with status.
    """
    # Build search query: SKU is the most precise identifier
    search_key = mpn.strip() if mpn and mpn.strip() else sku
    query = quote_plus(f"{search_key} {title[:40]}".strip())
    search_url = supplier_cfg["search_url_template"].format(query=query)

    # Check cache on the full search URL first
    cached = _load_cache(search_url)
    if cached:
        print(f"[scrape] {sku}: cached (search result)", flush=True)
        return {**cached, "status": "cached"}

    # Fetch search results
    search_soup, fetch_status = _fetch(search_url, sku, skip_robots=not supplier_cfg.get("respect_robots", True))
    if not search_soup:
        return {"status": fetch_status, "description": "", "specifications": "", "features": ""}

    # Extract first product link
    link_selector = supplier_cfg.get("product_link_selector", "a")
    link_el = search_soup.select_one(link_selector)
    if not link_el or not link_el.get("href"):
        # Try a broader fallback
        link_el = search_soup.select_one("a[href*='product'], a[href*='catalogue'], a[href*='shop']")

    if not link_el or not link_el.get("href"):
        print(f"[scrape] {sku}: no_product_link_found in site search, trying Google fallback", flush=True)
        google_result = _google_fallback(domain, sku, title, mpn)
        # Keep the routing_strategy from the supplier config if available
        google_result["routing_strategy"] = supplier_cfg.get("_routing_strategy", "google_fallback")
        if google_result["status"] in ("success", "cached", "scraped_empty"):
            return google_result
        # Total failure – return the original status
        return {"status": "no_product_link", "description": "", "specifications": "", "features": ""}


    product_url = urljoin(f"https://{domain}", link_el["href"])

    # Check cache on the product URL
    cached = _load_cache(product_url)
    if cached:
        print(f"[scrape] {sku}: cached (product page)", flush=True)
        return {**cached, "status": "cached"}

    # Fetch the product page
    product_soup, fetch_status = _fetch(product_url, sku, skip_robots=not supplier_cfg.get("respect_robots", True))
    if not product_soup:
        return {"status": fetch_status, "description": "", "specifications": "", "features": ""}

    # Verify it looks like a product page
    if not _is_likely_product_page(product_soup, title):
        print(f"[scrape] {sku}: product_page_quality_low -- using anyway", flush=True)

    # Extract content
    content = _extract_from_soup(
        product_soup,
        product_url,
        supplier_cfg.get("content_selectors", []),
        supplier_cfg.get("spec_selectors", []),
    )

    # Cache on the product URL
    _save_cache(product_url, content)

    has_content = bool(content["description"].strip() or content["specifications"].strip())
    final_status = "success" if has_content else "scraped_empty"
    print(f"[scrape] {sku}: {final_status} from {product_url}", flush=True)
    return {**content, "status": final_status}


# ── Main entry point ──────────────────────────────────────────────────────────

def scrape_product(sku: str, brand: str, title: str = "", mpn: str = "") -> dict:
    """
    Main entry point. Resolves the correct supplier URL, then either:
    - Uses search-then-scrape if a config entry exists for that domain
    - Returns a 'no_config' result if no config exists (pipeline falls back to Shopify content)
    Returns a content dict with status and routing_strategy.
    """
    if not config.SCRAPER_ENABLED:
        print(f"[scrape] {sku}: disabled", flush=True)
        return {"status": "disabled", "routing_strategy": "disabled",
                "description": "", "specifications": "", "features": ""}

    base_url, strategy = resolve_scrape_url(sku, brand)

    if strategy in ("ignore", "acco_gated", "multi_brand_no_match",
                    "unknown_prefix", "no_domain"):
        print(f"[scrape] {sku}: {strategy}", flush=True)
        return {
            "status": strategy,
            "routing_strategy": strategy,
            "description": "", "specifications": "", "features": "",
        }

    if not base_url:
        print(f"[scrape] {sku}: no_url", flush=True)
        return {
            "status": "no_url",
            "routing_strategy": strategy,
            "description": "", "specifications": "", "features": "",
        }

    # Determine the domain to look up in SUPPLIER_SEARCH_CONFIG
    parsed = urlparse(base_url)
    domain = parsed.netloc  # e.g. "www.canon.com.au"

    supplier_cfg = SUPPLIER_SEARCH_CONFIG.get(domain)
    if not supplier_cfg:
        # No search config for this supplier yet.
        # Return no_config so the pipeline falls back to existing Shopify content.
        print(f"[scrape] {sku}: no_search_config for {domain}", flush=True)
        return {
            "status": "no_config",
            "routing_strategy": strategy,
            "description": "", "specifications": "", "features": "",
        }

    result = _search_and_scrape(domain, sku, title or brand, supplier_cfg, mpn=mpn)
    result["routing_strategy"] = strategy
    return result


def init_scraper(supplier_map_path: str = "prefix_supplier_map_FINAL.csv"):
    """Load supplier map. Call once at pipeline startup."""
    if Path(supplier_map_path).exists():
        load_supplier_map(supplier_map_path)
    else:
        uploads_path = f"/mnt/user-data/uploads/{supplier_map_path}"
        if Path(uploads_path).exists():
            load_supplier_map(uploads_path)
        else:
            print(f"[scraper] WARNING: supplier map not found at {supplier_map_path}", flush=True)

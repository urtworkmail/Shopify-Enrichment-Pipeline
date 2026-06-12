"""
scraper.py -- Supplier page scraper with brand-based routing.

Key corrections from handoff:
    - MULTI-BRAND prefixes (DS, GN, AM, GS, LA, CC) route by product BRAND, not prefix domain
    - ACCO prefixes (AA, PQ, CU) are login-gated -- skip for now (no creds)
    - IGNORE prefixes skip scraping entirely
    - Results cached on disk to avoid re-fetching on resume
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from config import config
from supplier_router import resolve_scrape_url, load_supplier_map

CACHE_DIR = "output/scrape_cache"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MegaOfficeScraper/1.0; "
        "+https://megaofficesupplies.com.au)"
    )
}

_robots_cache: dict[str, bool] = {}


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
        json.dump(data, f)


def _is_allowed(base_url: str) -> bool:
    if base_url in _robots_cache:
        return _robots_cache[base_url]
    try:
        robots_url = base_url.rstrip("/") + "/robots.txt"
        # Use requests with a hard timeout instead of RobotFileParser.read()
        # which uses urllib internally with NO timeout and can hang forever.
        resp = requests.get(robots_url, headers=HEADERS, timeout=(8, 10))
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(resp.text.splitlines())
        allowed = rp.can_fetch(HEADERS["User-Agent"], base_url)
    except Exception:
        # Any error (timeout, connection refused, parse fail) -> assume allowed
        allowed = True
    _robots_cache[base_url] = allowed
    return allowed


def _extract_content(soup: BeautifulSoup, url: str) -> dict:
    """Extract structured product content from a supplier page."""
    result = {
        "description": "",
        "specifications": "",
        "features": "",
        "source_url": url,
    }

    desc_selectors = [
        ".product-description", ".product__description",
        "[data-product-description]", "#product-description",
        ".product-details__description", ".description",
        ".product-detail__description", ".product-info__description",
    ]
    for sel in desc_selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            result["description"] = el.get_text(separator="\n", strip=True)
            break

    spec_selectors = [
        ".product-specs", ".specifications", ".product__specs",
        ".spec-table", "table.specs", ".product-specifications",
        ".product-details__specs",
    ]
    for sel in spec_selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            result["specifications"] = el.get_text(separator="\n", strip=True)
            break

    feature_selectors = [
        ".product-features", ".features-list", ".key-features",
        ".product__features", ".product-highlights",
    ]
    for sel in feature_selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            result["features"] = el.get_text(separator="\n", strip=True)
            break

    # Fallback to main content area
    if not any([result["description"], result["specifications"], result["features"]]):
        main = (
            soup.find("main") or soup.find("article")
            or soup.find(id="content") or soup.find("body")
        )
        if main:
            result["description"] = main.get_text(separator="\n", strip=True)[:3000]

    return result


def scrape_url(url: str, sku: str = "", connect_timeout: int = 10, read_timeout: int = 30) -> dict:
    """
    Scrape a specific URL. Returns content dict with status field.
    Uses a hard (connect, read) timeout to prevent hangs.
    Logs every attempt with timing.
    """
    cached = _load_cache(url)
    if cached:
        print(f"[scrape] {sku}: cached")
        return {**cached, "status": "cached"}

    if not _is_allowed(url):
        print(f"[scrape] {sku}: blocked_robots (not allowed)")
        return {"status": "blocked_robots", "description": "", "specifications": "", "features": ""}

    print(f"[scrape] {sku}: trying {url}")
    t0 = time.time()

    try:
        time.sleep(config.SCRAPER_DELAY_SECONDS)
        resp = requests.get(url, headers=HEADERS, timeout=(connect_timeout, read_timeout))

        elapsed = time.time() - t0

        if resp.status_code == 403:
            print(f"[scrape] {sku}: blocked_403 ({elapsed:.1f}s)")
            return {"status": "blocked_403", "description": "", "specifications": "", "features": ""}
        if resp.status_code == 404:
            print(f"[scrape] {sku}: not_found ({elapsed:.1f}s)")
            return {"status": "not_found", "description": "", "specifications": "", "features": ""}
        if resp.status_code != 200:
            print(f"[scrape] {sku}: http_{resp.status_code} ({elapsed:.1f}s)")
            return {"status": f"http_{resp.status_code}", "description": "", "specifications": "", "features": ""}

        soup = BeautifulSoup(resp.text, "html.parser")
        content = _extract_content(soup, url)
        _save_cache(url, content)
        print(f"[scrape] {sku}: success ({elapsed:.1f}s)")
        return {**content, "status": "success"}

    except requests.exceptions.Timeout:
        elapsed = time.time() - t0
        print(f"[scrape] {sku}: timeout ({elapsed:.1f}s)")
        return {"status": "timeout", "description": "", "specifications": "", "features": ""}
    except requests.exceptions.ConnectionError:
        elapsed = time.time() - t0
        print(f"[scrape] {sku}: connection_error ({elapsed:.1f}s)")
        return {"status": "connection_error", "description": "", "specifications": "", "features": ""}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[scrape] {sku}: error ({elapsed:.1f}s) - {str(e)[:80]}")
        return {"status": f"error:{str(e)[:80]}", "description": "", "specifications": "", "features": ""}


def scrape_product(sku: str, brand: str, title: str = "") -> dict:
    """
    Main entry point. Resolves the correct URL then scrapes.
    Returns content dict with status and routing_strategy.
    Enforces a hard 60-second wall-clock timeout via a thread so that
    no robots.txt fetch, DNS hang, or socket stall can freeze the pipeline.
    """
    import concurrent.futures

    if not config.SCRAPER_ENABLED:
        print(f"[scrape] {sku}: disabled")
        return {"status": "disabled", "routing_strategy": "disabled"}

    def _do_scrape():
        base_url, strategy = resolve_scrape_url(sku, brand)

        if strategy in ("ignore", "acco_gated", "multi_brand_no_match",
                        "unknown_prefix", "no_domain"):
            print(f"[scrape] {sku}: {strategy}")
            return {
                "status": strategy,
                "routing_strategy": strategy,
                "description": "",
                "specifications": "",
                "features": "",
            }

        if not base_url:
            print(f"[scrape] {sku}: no_url")
            return {
                "status": "no_url",
                "routing_strategy": strategy,
                "description": "",
                "specifications": "",
                "features": "",
            }

        result = scrape_url(base_url, sku=sku)
        result["routing_strategy"] = strategy
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_scrape)
        try:
            return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            print(f"[scrape] {sku}: hard_timeout (>60s, killed)")
            return {
                "status": "hard_timeout",
                "routing_strategy": "unknown",
                "description": "",
                "specifications": "",
                "features": "",
            }


def init_scraper(supplier_map_path: str = "prefix_supplier_map_FINAL.csv"):
    """Load supplier map. Call once at pipeline startup."""
    if Path(supplier_map_path).exists():
        load_supplier_map(supplier_map_path)
    else:
        uploads_path = f"/mnt/user-data/uploads/{supplier_map_path}"
        if Path(uploads_path).exists():
            load_supplier_map(uploads_path)
        else:
            print(f"[scraper] WARNING: supplier map not found at {supplier_map_path}")
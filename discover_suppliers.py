"""
discover_suppliers.py -- Auto-discovery of supplier search URL patterns.

Probes each unconfigured supplier domain with common search URL templates,
checks if the response contains product-like links, and generates a
SUPPLIER_SEARCH_CONFIG entry for working patterns.
"""

import csv
import json
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.5",
}

# Common search URL patterns to try (in order of likelihood)
SEARCH_TEMPLATES = [
    "/search?q={query}",
    "/search?q={query}&type=product",
    "/catalogsearch/result/?q={query}",
    "/products/search?q={query}",
    "/shop/search?q={query}",
    "/?s={query}&post_type=product",
    "/?s={query}",
    "/search.aspx?q={query}",
    "/Search/Results?q={query}",
    "/pages/search-results-page?q={query}",
]

# Words that suggest a link is a product link
PRODUCT_LINK_SIGNALS = ["/product/", "/products/", "/item/", "/p/", "/shop/", "/catalogue/"]

# Words that suggest a page has no real results
NO_RESULTS_SIGNALS = ["no results", "no products found", "0 results", "sorry, no", "nothing found"]


def _is_product_link(href: str, text: str) -> bool:
    """Heuristic: does this link look like it points to a product page?"""
    href_lower = href.lower()
    return any(s in href_lower for s in PRODUCT_LINK_SIGNALS)


def _has_product_links(html: str) -> bool:
    """Quick check: does the HTML contain likely product links?"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)
    product_links = [a for a in links if _is_product_link(a["href"], a.get_text(strip=True))]
    return len(product_links) >= 1


def _has_no_results(html: str) -> bool:
    """Check if the page indicates no search results."""
    text_lower = html.lower()
    return any(s in text_lower for s in NO_RESULTS_SIGNALS)


def probe_domain(domain: str, test_query: str = "test", delay: float = 2.0) -> dict | None:
    """
    Probe a single domain for a working search URL.
    Returns a config dict if a working pattern is found, None otherwise.
    """
    print(f"\n[discover] Probing {domain} with query '{test_query}'...")

    for template in SEARCH_TEMPLATES:
        url = f"https://{domain}{template.format(query=quote_plus(test_query))}"
        try:
            time.sleep(delay)
            resp = requests.get(url, headers=HEADERS, timeout=(10, 20), allow_redirects=True)

            if resp.status_code != 200:
                print(f"  {template} -> HTTP {resp.status_code}")
                continue

            if _has_no_results(resp.text):
                print(f"  {template} -> 200 but 'no results' found")
                continue

            if _has_product_links(resp.text):
                print(f"  {template} -> 200 WITH product links -- ACCEPTED")
                return {
                    "search_url_template": f"https://{domain}{template}",
                    "product_link_selector": "a[href*='/product/'], a[href*='/products/'], a[href*='/item/']",
                    "respect_robots": False,
                    "content_selectors": [
                        ".product-description", ".product__description",
                        "[data-product-description]", "#product-description",
                        ".product-details__description", ".description",
                    ],
                    "spec_selectors": [
                        ".product-specifications", ".specifications",
                        ".product-specs", ".spec-table", "table.specs",
                    ],
                    "auto_discovered": True,
                }
            else:
                print(f"  {template} -> 200 but no product links found")

        except requests.exceptions.Timeout:
            print(f"  {template} -> timeout")
        except requests.exceptions.ConnectionError:
            print(f"  {template} -> connection error")
        except Exception as e:
            print(f"  {template} -> error: {str(e)[:60]}")

    return None


def load_existing_config(config_path: str = None) -> set[str]:
    """Extract already-configured domains from scraper.py."""
    configured = set()
    try:
        # Try to import from scraper
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from scraper import SUPPLIER_SEARCH_CONFIG
        configured = set(SUPPLIER_SEARCH_CONFIG.keys())
    except Exception:
        pass
    return configured


def load_prefix_map_domains(csv_path: str = "prefix_supplier_map_FINAL.csv") -> list[str]:
    """Extract unique domains from the prefix supplier map."""
    domains = set()
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            domain = row.get("supplier_domain", "").strip()
            strategy = row.get("scrape_strategy", "").strip()
            if domain and strategy not in ("ignore", "acco_gated"):
                # Take first domain if multiple
                domain = domain.split("|")[0].split(";")[0].strip()
                if domain.startswith("http"):
                    from urllib.parse import urlparse
                    domain = urlparse(domain).netloc
                domains.add(domain)
    return sorted(domains)


def main():
    print("=" * 60)
    print("SUPPLIER SEARCH AUTO-DISCOVERY TOOL")
    print("=" * 60)

    existing = load_existing_config()
    print(f"\nAlready configured: {len(existing)} domains")
    for d in sorted(existing):
        print(f"  - {d}")

    all_domains = load_prefix_map_domains()
    to_probe = [d for d in all_domains if d not in existing]
    print(f"\nDomains to probe: {len(to_probe)}")
    for d in to_probe:
        print(f"  - {d}")

    discovered = {}
    failed = []

    for i, domain in enumerate(to_probe, 1):
        print(f"\n[{i}/{len(to_probe)}] {domain}")
        result = probe_domain(domain, test_query="test")
        if result:
            discovered[domain] = result
        else:
            failed.append(domain)

    # Save results
    output = {
        "discovered": discovered,
        "failed": failed,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    output_path = "output/discovered_suppliers.json"
    Path("output").mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print("\n" + "=" * 60)
    print(f"RESULTS: {len(discovered)} discovered, {len(failed)} failed")
    print(f"Saved to {output_path}")
    print("=" * 60)

    if discovered:
        print("\nTo add these to scraper.py, copy the entries from discovered_suppliers.json")
        print("into the SUPPLIER_SEARCH_CONFIG dictionary.")
    if failed:
        print("\nFailed domains (need manual patterns from client):")
        for d in failed:
            print(f"  - {d}")


if __name__ == "__main__":
    main()
# Prompt for Opus 4.8 - Improved Scraper System

## Context
We have a product enrichment pipeline that scrapes supplier websites to get product descriptions and specifications. The current system has issues - it only works for ~4,500 products out of ~28,000.

## Current Problems

1. **Status: not_found** - Supplier search doesn't find products by SKU or product name
2. **Status: no_config** - No search URL pattern defined for the supplier
3. **Status: cached** - Returns category pages instead of product pages (wrong selectors)
4. **Status: multi_brand_no_match** - Brand not in routing dictionary
5. **Status: blocked_robots** - Site blocks via robots.txt
6. **Status: http_521** - Server errors

## What We Have

### Database Fields Available for Search:
- Product Name (title)
- MPN (Manufacturer Part Number)
- SKU (our internal SKU)
- Vendor/Brand

### Current Scraper Structure:
```python
SUPPLIER_SEARCH_CONFIG: dict[str, dict] = {
    "domain.com": {
        "search_url_template": "https://domain.com/search?q={query}",
        "product_link_selector": ".product-name a",
        "content_selectors": [".product-description"],
        "spec_selectors": [".specifications"],
    },
}
```

## Task for Opus

Design and implement an improved scraper system that:

1. **Smarter URL Construction**
   - Try multiple URL construction strategies:
     - Direct search with product name
     - Direct search with MPN
     - Direct search with SKU
     - Try each until one works

2. **Better Product Matching**
   - Don't just take the first search result
   - Verify the product page matches our product (check title, MPN, specs)
   - Use fuzzy matching for product names

3. **Content Extraction**
   - Multiple fallback selectors for different page structures
   - Handle dynamic JavaScript-loaded content
   - Better handling of WooCommerce/Shopify sites

4. **Error Handling**
   - Exponential backoff retries
   - Handle HTTP 521 errors gracefully
   - Work around robots.txt blocks where possible

5. **Caching & Performance**
   - Aggressive caching of successful scrapes
   - Parallel scraping where allowed
   - Rate limiting to avoid blocks

## Suppliers to Focus On (Priority Order)

### Works (already working) - Keep as is:
- CD, FX, TN, PH, DO, KC, ZN, AR, SL, JA, FC, PE, GG

### Fix These (high impact):
- SR (Staedtler) - 1,216 products - search returns nothing
- VC (Visionchart) - 538 products - search returns nothing
- WE (Weatherdon) - 445 products - search returns nothing
- FM (Fellowes) - 335 products - search returns nothing
- JP (Deflecto) - 313 products - search returns nothing
- CO (Colby) - 257 products - search returns nothing
- BH (Hamelin) - 800 products - wrong selectors (category pages)
- AD (Avery) - 1,128 products - HTTP 521 errors (may recover)

### Maybe Fix (lower priority):
- Multi-brand: DS, GN, AM, SD, LA, CC, GS (~5,000 products)

## Expected Output

Design the improved scraper module that can:
1. Increase success rate above current ~16%
2. Handle more edge cases gracefully
3. Provide better diagnostics for failures
4. Be maintainable and extensible

Focus on the high-impact suppliers first where we know the products exist on their sites, just hard to find.
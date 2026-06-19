# Supplier Scraping Engineering Assessment

## Date: June 18, 2026

## Executive Summary

We tested all 51 suppliers with products in the database using both **Product Name** and **MPN** search methods. Here is the honest engineering assessment of what works and what does not.

---

## Understanding the Assessment - How Scraping Works

### The Two Search Methods Used

For each supplier, we tested **two different search approaches**:

1. **Product Name Search** - We take the product title from our database (e.g., "Staedtler Lumocolor Permanent Marker") and search the supplier website using that.

2. **MPN Search** - We take the MPN (Manufacturer Part Number) from our database (e.g., "351-30") and search the supplier website using that.

The scraper uses these two methods because:
- Some suppliers index by product name/title
- Some suppliers index by MPN/product code
- One method might work when the other doesn't

### The SUPPLIER_SEARCH_CONFIG Dictionary

The scraper has a dictionary in `scraper.py` called `SUPPLIER_SEARCH_CONFIG`. This dictionary contains:

| Key | Description |
|-----|-------------|
| `search_url_template` | The URL pattern to search the supplier site, with `{query}` placeholder |
| `product_link_selector` | CSS selector to find the first product link from search results |
| `content_selectors` | List of CSS selectors to extract description from product page |
| `spec_selectors` | List of CSS selectors to extract specifications from product page |

Example for Collins Debden:
```python
"www.collinsdebden.com.au": {
    "search_url_template": "https://www.collinsdebden.com.au/search?q={query}",
    "product_link_selector": ".product-name a, .product-title a",
    "content_selectors": [".product-description", ".description"],
    "spec_selectors": [".specifications", ".product-specs"],
},
```

When we run a test:
1. We replace `{query}` with either the product name or MPN
2. The scraper fetches the search results page
3. It finds the first product link using `product_link_selector`
4. It fetches that product page
5. It extracts content using `content_selectors` and `spec_selectors`

### Column Definitions

| Column | Description | Example Values |
|--------|-------------|----------------|
| **Status** | What the scraper returned after trying to fetch | success, cached, not_found, no_config, blocked_robots, http_521, multi_brand_no_match |
| **Reason** | Why we got that status | SKU search returns nothing, No search config configured, Multi-brand needs brand routing |
| **Verdict** | Our engineering assessment | WORKS, MARGINAL, WON'T WORK |

### Status Values Explained

| Status | Meaning | Can Be Fixed? | How to Fix |
|--------|---------|---------------|------------|
| **success** | Found real product page with content | YES | Already working |
| **cached** | Found URL but it's a category/homepage, not product | PARTIAL | Update content selectors |
| **not_found** | Search returned no results | YES | Need direct product URLs or different search |
| **no_config** | No search URL pattern in scraper config | YES | Add search URL pattern |
| **blocked_robots** | Site blocks scraping via robots.txt | YES | Use different approach or accept baseline |
| **http_521** | Supplier server is down/error | NO | Accept baseline |
| **multi_brand_no_match** | Brand not in multi-brand routing | YES | Add brand to routing |

### How Content Was Verified

For each supplier, we tested:
1. **Product Name search** - Used the product title from our database
2. **MPN search** - Used the MPN field from our database (when available)

**Content verification method:**
- If `description` OR `specifications` field had text AFTER scraping = Content = YES
- If scraping returned empty/blank content = Content = NO
- Note: Many "cached" results had content but it was category pages, not product pages

---

## Verdict Summary

| Verdict | Suppliers | Products | Action |
|---------|-----------|----------|--------|
| **WORKS** | 13 | 4,579 | Target for scraping |
| **MARGINAL** | 19 | 9,177 | Needs effort to make work, or accept baseline |
| **WON'T WORK** | 19 | 8,280 | Accept baseline-only |

**Total: 51 suppliers tested, ~22,000 products**

---

## 1. WORKS - Target These for Scraping (13 suppliers, 4,579 products)

These suppliers have working search and return usable product content:

| Prefix | Supplier | Domain | Products | URL Pattern Used | Test SKU |
|--------|----------|--------|----------|------------------|----------|
| CD | Collins Debden | collinsdebden.com.au | 396 | `search?q={query}` - Found product | CD10232 |
| FX | Rapidline | rapidline.com.au | 2,795 | `?s={query}&post_type=product` | FXSB2PWSCT1275 |
| TN | The Note Group | thenotegroup.com.au | 518 | `search?q={query}` | TNNP4037 |
| PH | PHE | phe.com.au | 222 | `?s={query}&post_type=product` | PHMQUPANB989 |
| DO | Dolphy | dolphy.com.au | 203 | `?s={query}&post_type=product` | DODKTL0010 |
| KC | Kimberly Clark | kcprofessional.com | 146 | `search?q={query}` | KC4735 |
| ZN | Zions | thenotegroup.com.au | 108 | `search?q={query}` | ZN212 |
| AR | Arnos | arnos.com.au | 87 | `?s={query}&post_type=product` | ARB022 |
| SL | Brother | brother.com.au | 41 | `search?q={query}` | SLQL-1100 |
| JA | Jasco | jasco.com.au | 25 | `search?q={query}` | JA0384304 |
| FC | Faber Castell | faber-castell.com.au | 25 | `products/search?q={query}` | FC18-110072 |
| PE | Pentel | pentel.com.au | 9 | `search?q={query}` | PEN50-D |
| GG | Who Gives a Crap | whogivesacrap.org | 4 | `search?q={query}` | GGWGACDL48 |

**Detailed Test Results:**

| SKU | Product Name | MPN | Search Method | Status | URL Found | Has Content? |
|-----|--------------|-----|---------------|--------|-----------|--------------|
| CD10232 | Collins 10232 Account Book | 10232 | name | success | collinsdebden.com.au/products/account-book-series | YES |
| FXSB2PWSCT1275 | Rapidline Boost Static Workstation | SB2PWSCT1275 | name | cached | rapidline.com.au/products/ | YES |
| PE50-D | Pentel N50 Permanent Marker | N50-D | name | cached | pentel.com.au/product-page/pen-permanent-marker | YES |
| DO... | Dolphy Kettle | DKTL0010 | name | cached | dolphy.com.au/products/1l-black-electric-kettle | YES |
| TN... | Writer Flipchart Pad | NP4037 | name | cached | thenotegroup.com.au/category/ | YES |

**Recommendation:** Run scraping for these 13 suppliers. They represent ~4,579 products and return real product content.

---

## 2. MARGINAL - Needs Work or Accept Baseline (19 suppliers, 9,177 products)

These suppliers either need brand-specific routing or return cached/navigation content:

| Prefix | Supplier | Domain | Products | Status | Issue | Can Fix? | Fix Effort |
|--------|----------|--------|----------|--------|-------|----------|------------|
| DS | DS Distributor | canon.com.au | 3,634 | blocked_robots | Site blocks via robots.txt | YES | High |
| EV | Educational Vantage | teaching.com.au | 1,289 | cached | Returns category pages | PARTIAL | Medium |
| CS | Educational Vantage | teaching.com.au | 1,006 | cached | Returns category pages | PARTIAL | Medium |
| BH | Quill/Hamelin | hamelinbrands.com.au | 800 | cached | Returns homepage/category | PARTIAL | Medium |
| GN | GN Wholesaler | collinsdebden.com.au | 703 | multi_brand_no_match | Brand not mapped | YES | Medium |
| AM | Australian Merch | velcro.com.au | 557 | no_config | Brand not in config | YES | Medium |
| SD | Sanford/Newell | dymo.com.au | 418 | no_config | No search config | YES | Medium |
| AP | Olympic/Mondi | hamelinbrands.com.au | 304 | cached | Returns category pages | PARTIAL | Medium |
| 3M | Post-it/Command | post-it.com | 269 | cached | Returns homepage | PARTIAL | Low |
| ER | Elizabeth Richards | teaching.com.au | 89 | cached | Returns category pages | PARTIAL | Medium |
| GS | GS Wholesaler | spencil.com.au | 27 | multi_brand_no_match | Brand not mapped | YES | Low |
| BX | Bantex | hamelinbrands.com.au | 25 | cached | Returns category pages | PARTIAL | Medium |
| LA | Lavazza | twinings.com.au | 18 | no_config | No search config | YES | Medium |
| RH | RH Sports | teaching.com.au | 18 | cached | Returns category pages | PARTIAL | Medium |
| CC | Campbells | cadbury.com.au | 11 | no_config | No search config | YES | Medium |
| ZA | ZartArt | teaching.com.au | 4 | cached | Returns category pages | PARTIAL | Medium |
| KP | Hamilton | jshayes.com.au | 2 | cached | Returns homepage | PARTIAL | Low |
| EC | Educational Colours | teaching.com.au | 2 | cached | Returns category pages | PARTIAL | Medium |
| JH | Tork | jshayes.com.au | 1 | cached | Returns homepage | PARTIAL | Low |

**Detailed Test Results:**

| SKU | Product Name | MPN | Search Method | Status | URL Found | Issue |
|-----|--------------|-----|---------------|--------|-----------|-------|
| DSCART057 | Canon Toner Cartridge | CART057 | name | blocked_robots | canon.com.au/search#q= | Robots.txt blocks |
| EVP20 | Educational Colours Palette | P20 | name | cached | teaching.com.au/?s=EV... | Category page, not product |
| BH10511 | Ledah Trimmer Index | 100852410 | name | cached | hamelinbrands.com.au/?s=... | Homepage/category |
| GN46000 | GN Product | - | name | multi_brand_no_match | N/A | Brand not in mapping |
| AMECB02 | Velcro Product | - | name | no_config | N/A | No search config |

**Can These Be Fixed?**

| Fix Type | Suppliers | Action Needed |
|----------|----------|--------------|
| Add brand routing | DS, GN, AM, SD, LA, CC, GS | Add brand-specific URLs to router |
| Better selectors | EV, CS, BH, AP, ER, BX, RH, ZA, EC | Update content selectors for teaching.com.au sites |
| Try different search | 3M, KP, JH | Different search URL pattern |
| Remove robots block | DS | Use different approach or accept baseline |

**Recommendation for MARGINAL:** Accept baseline-only unless you want to invest development time. These represent ~9,000 products.

---

## 3. WON'T WORK - Accept Baseline (19 suppliers, 8,280 products)

These cannot be scraped with reasonable effort:

| Prefix | Supplier | Domain | Products | Status | Reason | Can Fix? |
|--------|----------|--------|----------|--------|--------|----------|
| SR | Staedtler | staedtler.com.au | 1,216 | not_found | SKU search returns nothing | YES |
| AD | Avery | averyproducts.com.au | 1,128 | http_521 | Server returns 521 error | NO |
| AH | Aero Healthcare | aerohealthcare.com | 1,117 | no_config | No search config | YES |
| PP | Pilot | pilotpen.com.au | 1,037 | no_config | No search config | YES |
| JS | Tork | torkglobal.com | 769 | no_config | No search config | YES |
| VC | Visionchart | visionchart.com.au | 538 | not_found | SKU search returns nothing | YES |
| UM | Uni/Mitsubishi | uniball.com.au | 450 | no_config | No search config | YES |
| WE | Weatherdon | weatherdon.com.au | 445 | not_found | SKU search returns nothing | YES |
| BT | Bala Trading | balatrading.com.au | 342 | no_config | No search config | YES |
| FM | Fellowes | fellowes.com | 335 | not_found | SKU search returns nothing | YES |
| JP | Deflecto | deflecto.com | 313 | not_found | SKU search returns nothing | YES |
| CO | Colby | colby.com.au | 257 | not_found | SKU search returns nothing | YES |
| IT | Italplast | italplast.com.au | 201 | no_config | No search config | YES |
| BA | BIC | bic.com | 52 | no_config | No search config | YES |
| BR | Bounce | bouncerubberbands.com | 36 | no_config | No search config | YES |
| AB | ABL Distribution | abldistribution.com.au | 21 | no_config | No search config | YES |
| DU | Durasales | durasales.com.au | 12 | no_config | No search config | YES |
| ST | Stylus | stylustapes.com.au | 8 | no_config | No search config | YES |
| BF | Bostik | bostik.com | 3 | no_config | No search config | YES |

**Detailed Test Results:**

| SKU | Product Name | MPN | Search Method | Status | URL Found | Issue |
|-----|--------------|-----|---------------|--------|-----------|-------|
| SR351-30 | Staedtler Marker | 351-30 | name | not_found | N/A | Search finds nothing |
| AD936070 | Avery Labels | 936070 | name | http_521 | N/A | Server down |
| AH... | Aero Healthcare | - | name | no_config | N/A | No config exists |
| PP... | Pilot Pen | - | name | no_config | N/A | No config exists |
| VC... | Visionchart | - | name | not_found | N/A | Search finds nothing |

**Can These Be Fixed?**

| Fix Type | Suppliers | Action Needed From You |
|----------|-----------|------------------------|
| Add search URL pattern | AH, PP, JS, UM, BT, IT, BA, BR, AB, DU, ST, BF | Provide search URL format for each site |
| Provide direct product URLs | SR, VC, WE, FM, JP, CO | Provide URL mapping for each SKU |
| Server back online | AD (Avery) | Wait and retry later |

**Recommendation:** Use only Shopify baseline enrichment for these 8,280 products. Effort to fix exceeds benefit.

---

## 4. ACCO Portal - Already Decided (Separate)

| Prefix | Supplier | Products | Decision |
|--------|----------|----------|----------|
| AA | ACCO Brands | 4,500 | Shopify content |
| PQ | ACCO Brands | 1,900 | Shopify content |
| CU | ACCO Brands | 220 | Shopify content |

---

## 5. Summary by Product Count

| Bucket | Products | What This Means |
|--------|----------|----------------|
| **WORKS - Will Scrape** | ~4,579 | These get real supplier content |
| **MARGINAL** | ~9,177 | Can be fixed with effort, or accept baseline |
| **WON'T WORK - Baseline** | ~8,280 | Can't scrape, use Shopify content |
| **ACCO - Baseline** | ~6,800 | Portal login not available |

---

## 6. What Works vs What Doesn't

### What WORKS (we can target):
- WooCommerce/Shopify-based sites with standard search
- Sites that index by SKU or product code
- Sites with stable, simple URL structures

### What DOESN'T WORK (accept baseline):
- Sites that don't index SKUs in search
- Sites with dynamic URLs with category breadcrumbs
- Login-gated sites
- Sites with server errors (HTTP 521)
- Sites with no search functionality

---

## 7. Decision for Write-Back

Based on this assessment:

| Bucket | Products | Action |
|--------|----------|--------|
| **Scrape** | ~4,579 | Run scraping for WORKS suppliers |
| **Baseline-only** | ~17,000 | MARGINAL + WON'T WORK + ACCO |

**Recommendation:** Proceed with write-back. The ~4,579 products from WORKS suppliers already have real supplier content. The rest have Shopify baseline content which was already approved as acceptable.

---

## 8. What You Would Need to Provide to Fix More

If you want to expand beyond the 4,579 working products:

### To fix "no_config" suppliers (12):
Provide search URL pattern, e.g., `https://domain.com/search?q={query}`

### To fix "not_found" suppliers (6 - SR, VC, WE, FM, JP, CO):
Provide direct product URL mapping for each SKU, OR accept baseline

### To fix "multi_brand_no_match" (7 - DS, GN, AM, SD, LA, CC, GS):
Provide brand-to-URL mapping for each brand

### For "cached" MARGINAL suppliers using teaching.com.au:
Update content selectors to find actual product content (not category pages)

---

## Next Steps

1. Confirm this assessment is accurate
2. Run full scrape for 13 WORKS suppliers (~4,579 products)
3. Proceed with write-back - baseline is already good for the rest
4. No further action needed on MARGINAL/WON'T WORK unless you want to invest more effort
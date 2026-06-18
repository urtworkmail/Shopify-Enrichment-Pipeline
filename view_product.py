"""
view_product.py – Open a product detail page directly in the browser.
Usage:   python view_product.py "SR100A G6"
"""
import sys, json, webbrowser, tempfile, os
from database import get_db, Product, Enrichment

if len(sys.argv) < 2:
    print("Usage: python view_product.py <SKU>")
    sys.exit(1)

sku = sys.argv[1]

with get_db() as db:
    product = db.query(Product).filter_by(sku=sku).first()
    if not product:
        print(f"SKU '{sku}' not found in database.")
        sys.exit(1)

    enrichment = (
        db.query(Enrichment)
        .filter_by(sku=sku, status="success")
        .order_by(Enrichment.created_at.desc())
        .first()
    )
    if not enrichment:
        print(f"No successful enrichment for '{sku}'.")
        sys.exit(1)

    # Extract all needed data *inside* the session context
    title = product.title
    tier = enrichment.tier
    scrape_status = enrichment.scrape_status
    cost_usd = enrichment.cost_usd

    existing = json.dumps(product.existing_content or {}, indent=2, ensure_ascii=False)
    scraped  = json.dumps(enrichment.scraped_content or {}, indent=2, ensure_ascii=False)
    enriched = json.dumps(enrichment.enriched_data or {}, indent=2, ensure_ascii=False)

    alt_texts = ""
    if isinstance(enrichment.enriched_data, dict) and isinstance(enrichment.enriched_data.get("image_alt_texts"), list):
        alt_texts = "<br>".join(enrichment.enriched_data["image_alt_texts"])

# Now everything is safe to use
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{sku} – Product Detail</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0a0a0a; color: #ccc; margin: 20px; max-width: 900px; }}
  h1 {{ color: #fff; }}
  h2 {{ color: #aaa; margin-top: 30px; }}
  pre {{ background: #111; padding: 15px; border-radius: 8px; overflow-x: auto;
         white-space: pre-wrap; border: 1px solid #333; font-size: 13px; line-height: 1.5; }}
  .meta {{ color: #888; font-size: 13px; margin-bottom: 8px; }}
  .meta span {{ color: #ccc; }}
</style>
</head>
<body>
<h1>{sku} — {title}</h1>
<div class="meta">
  <strong>Tier:</strong> <span>{tier}</span> &nbsp;|&nbsp;
  <strong>Scrape:</strong> <span>{scrape_status}</span> &nbsp;|&nbsp;
  <strong>Cost:</strong> <span>${cost_usd:.4f}</span>
</div>

<h2>Shopify Existing Content</h2>
<pre>{existing}</pre>

<h2>Scraped Content</h2>
<pre>{scraped}</pre>

<h2>Enriched JSON</h2>
<pre>{enriched}</pre>

{f'''<h2>Image Alt Texts</h2>
<p style="font-size:14px; line-height:1.8;">{alt_texts}</p>''' if alt_texts else ""}
</body>
</html>"""

tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode='w', encoding="utf-8")
tmp.write(html)
tmp.close()
webbrowser.open(f"file:///{tmp.name.replace(os.sep, '/')}")
print(f"Opened detail for {sku} in your browser.")
import requests, json
from database import SessionLocal, Product, Enrichment
from token_manager import get_headers
from config import config

sku = 'PEN50-D'
db = SessionLocal()
product = db.query(Product).filter_by(sku=sku).first()
enrichment = db.query(Enrichment).filter_by(sku=sku, status='success').order_by(Enrichment.created_at.desc()).first()
db.close()

if not product or not enrichment:
    print('Product or enrichment missing')
    exit()

headers = get_headers()
query = """
query getProduct($id: ID!) {
  product(id: $id) {
    title
    seo { title description }
    images(first: 5) { edges { node { id altText url } } }
    metafields(first: 20) { edges { node { namespace key value } } }
  }
}
"""
resp = requests.post(
    config.shopify_graphql_url,
    headers=headers,
    json={"query": query, "variables": {"id": product.shopify_product_id}},
    timeout=30,
)
shopify = resp.json().get("data", {}).get("product", {})

local = enrichment.enriched_data or {}

print("=== TITLE ===")
print("Shopify:", shopify.get("title", ""))
print("Local:  ", local.get("title", ""))

print("\n=== SEO TITLE ===")
print("Shopify:", shopify.get("seo", {}).get("title", ""))
print("Local:  ", local.get("seo_title", ""))

print("\n=== IMAGES (filenames + alt texts) ===")
for i, edge in enumerate(shopify.get("images", {}).get("edges", []), 1):
    img = edge["node"]
    # Extract filename from URL
    filename = img["url"].split("/")[-1].split("?")[0]
    print(f"  Image {i}:")
    print(f"    Filename: {filename}")
    print(f"    Alt text: {img.get('altText', 'NONE')}")

print("\n=== METAFIELDS ===")
metafield_map = {}
for edge in shopify.get("metafields", {}).get("edges", []):
    n = edge["node"]
    metafield_map[f"{n['namespace']}.{n['key']}"] = n["value"][:100]

for key in ["custom.key_features", "custom.applications", "custom.specifications",
            "custom.pack_size", "custom.unit", "custom.supplier_code",
            "mm-google-shopping.google_product_category"]:
    print(f"  {key}: {'PRESENT' if key in metafield_map else 'MISSING'}")
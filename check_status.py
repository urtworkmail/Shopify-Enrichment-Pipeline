import requests
from database import SessionLocal, Product
from token_manager import get_headers
from config import config

skus = ['AA6502', 'CU606322', 'PQ120073', 'FXPIN129 BE', 'PEN50-A']
db = SessionLocal()
headers = get_headers()

query = """
query getProduct($id: ID!) {
  product(id: $id) {
    handle
    status
    publishedOnCurrentPublication
    onlineStoreUrl
  }
}
"""

for sku in skus:
    product = db.query(Product).filter_by(sku=sku).first()
    if not product or not product.shopify_product_id:
        print(f"{sku}: NO PRODUCT ID")
        continue

    resp = requests.post(
        config.shopify_graphql_url,
        headers=headers,
        json={"query": query, "variables": {"id": product.shopify_product_id}},
        timeout=30,
    )
    if resp.status_code == 200:
        data = resp.json().get("data", {}).get("product", {})
        print(f"{sku}:")
        print(f"  Handle: {data.get('handle', 'NONE')}")
        print(f"  Status: {data.get('status', 'UNKNOWN')}")
        print(f"  Published: {data.get('publishedOnCurrentPublication', 'UNKNOWN')}")
        if data.get("onlineStoreUrl"):
            print(f"  URL: {data['onlineStoreUrl']}")
        else:
            print(f"  URL: https://megaofficesupplies.com.au/products/{data.get('handle', '')}")
    else:
        print(f"{sku}: HTTP {resp.status_code}")

db.close()
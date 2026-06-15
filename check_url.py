import requests, json
from database import SessionLocal, Product
from token_manager import get_headers
from config import config

sku = 'AA6502'
db = SessionLocal()
p = db.query(Product).filter_by(sku=sku).first()
db.close()
headers = get_headers()

query = """
query getProduct($id: ID!) {
  product(id: $id) {
    handle
    status
    onlineStoreUrl
  }
}
"""
resp = requests.post(
    config.shopify_graphql_url,
    headers=headers,
    json={"query": query, "variables": {"id": p.shopify_product_id}},
    timeout=30,
)
print('Status:', resp.status_code)
print('Response:', json.dumps(resp.json(), indent=2))
import requests, json
from database import SessionLocal, Product, Enrichment
from token_manager import get_headers
from config import config
from validator import prepare_metafields

sku = 'PEN50-A'
db = SessionLocal()
product = db.query(Product).filter_by(sku=sku).first()
enrichment = db.query(Enrichment).filter_by(sku=sku, status='success').order_by(Enrichment.created_at.desc()).first()
if not product or not enrichment:
    print('SKU not found or not enriched'); db.close(); exit()
headers = get_headers()
enriched = enrichment.enriched_data or {}
errors = []

# productUpdate
mut_prod = """
mutation call($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product { id title }
    userErrors { field message }
  }
}
"""
vars_prod = {
    'product': {
        'id': product.shopify_product_id,
        'title': enriched.get('title', product.title or ''),
        'descriptionHtml': enriched.get('body_html', ''),
        'vendor': product.vendor or '',
        'tags': enriched.get('tags', []),
        'seo': {
            'title': enriched.get('seo_title', ''),
            'description': enriched.get('seo_description', '')
        }
    }
}
resp = requests.post(config.shopify_graphql_url, headers=headers, json={'query': mut_prod, 'variables': vars_prod}, timeout=30)
if resp.status_code == 200:
    errs = resp.json().get('data', {}).get('productUpdate', {}).get('userErrors', [])
    if errs:
        errors.extend([f"productUpdate: {e['message']}" for e in errs])
    else:
        print('productUpdate OK')
else:
    errors.append(f'productUpdate HTTP {resp.status_code}')

# metafieldsSet
mut_mf = """
mutation call($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id namespace key value }
    userErrors { field message }
  }
}
"""
metafields = prepare_metafields(enriched)
for mf in metafields:
    mf['ownerId'] = product.shopify_product_id
resp = requests.post(config.shopify_graphql_url, headers=headers, json={'query': mut_mf, 'variables': {'metafields': metafields}}, timeout=30)
if resp.status_code == 200:
    errs = resp.json().get('data', {}).get('metafieldsSet', {}).get('userErrors', [])
    if errs:
        errors.extend([f"metafieldsSet: {e['message']}" for e in errs])
    else:
        print('metafieldsSet OK')
else:
    errors.append(f'metafieldsSet HTTP {resp.status_code}')

# Update enrichment status
if errors:
    enrichment.writeback_status = 'failed'
    enrichment.writeback_error = '; '.join(errors)
    print('Writeback failed:', enrichment.writeback_error)
else:
    enrichment.writeback_status = 'success'
    enrichment.writeback_error = ''
    print('Writeback success – passes: productUpdate, metafieldsSet, fileUpdate (skipped)')

db.commit()
db.close()
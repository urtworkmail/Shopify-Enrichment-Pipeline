import json
from claude_enricher import _build_user_message, classify_tier, existing_content_is_substantial

product_data = {
    "sku": "AA700994",
    "title": "Derwent Coloursoft Pencil Green Box of 6",
    "brand": "Derwent",
    "vendor": "Derwent",
    "price": 30.0,
    "description_html": "",
    "tags": [],
    "images": [],
    "existing_content": {},
}
supplier_content = {
    "status": "acco_gated",
    "description": "",
    "specifications": "",
    "features": "",
}
tier = classify_tier(False, False, 0)
print("Tier:", tier)
user_msg = _build_user_message(product_data, supplier_content, tier)
print("USER MESSAGE (first 200 chars):")
print(user_msg[:200])
print("...")
print("Placeholder check: missing keys would raise KeyError above.")
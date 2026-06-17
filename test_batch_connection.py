import requests
from config import config

url = "https://api.anthropic.com/v1/messages/batches"
headers = {
    "x-api-key": config.ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

# Submit a minimal, valid batch request
payload = {
    "requests": [
        {
            "custom_id": "test-1",
            "params": {
                "model": config.CLAUDE_MODEL,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Say hi"}],
            },
        }
    ]
}

resp = requests.post(url, headers=headers, json=payload, timeout=30)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:500]}")
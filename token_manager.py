"""
token_manager.py -- Shopify client credentials token manager.

Mints a fresh token on startup, persists it to the DB,
and auto-refreshes before expiry. All modules call get_token()
to get the current live token -- no manual credential handling needed.
"""

import threading
import time
from datetime import datetime, timedelta

import requests

from config import config
from database import get_db, ShopifyToken

# Refresh 5 minutes before expiry to avoid mid-request failures
EXPIRY_BUFFER_SECONDS = 300
TOKEN_LIFETIME_HOURS = 24

_lock = threading.Lock()
_cached_token: str = ""
_expires_at: datetime = datetime.utcnow()


def _mint_token() -> tuple[str, datetime]:
    """
    POST client_id + client_secret to Shopify and get a fresh access token.
    Returns (token, expires_at).
    """
    resp = requests.post(
        config.shopify_token_url,
        json={
            "client_id": config.SHOPIFY_CLIENT_ID,
            "client_secret": config.SHOPIFY_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in Shopify response: {data}")

    expires_at = datetime.utcnow() + timedelta(hours=TOKEN_LIFETIME_HOURS)
    return token, expires_at


def _persist_token(token: str, expires_at: datetime):
    """Save the new token to DB, deactivate old ones."""
    with get_db() as db:
        db.query(ShopifyToken).filter_by(is_active=True).update({"is_active": False})
        db.add(ShopifyToken(
            access_token=token,
            minted_at=datetime.utcnow(),
            expires_at=expires_at,
            is_active=True,
        ))


def _load_from_db() -> tuple[str, datetime] | None:
    """Try to load a still-valid token from DB (avoids redundant minting on restart)."""
    with get_db() as db:
        row = (
            db.query(ShopifyToken)
            .filter_by(is_active=True)
            .order_by(ShopifyToken.minted_at.desc())
            .first()
        )
        if row and row.expires_at > datetime.utcnow() + timedelta(seconds=EXPIRY_BUFFER_SECONDS):
            return row.access_token, row.expires_at
    return None


def _refresh():
    """Mint a new token and update global cache + DB."""
    global _cached_token, _expires_at
    print("[token] Minting fresh Shopify token ...")
    token, expires_at = _mint_token()
    _persist_token(token, expires_at)
    _cached_token = token
    _expires_at = expires_at
    print(f"[token] Token valid until {expires_at.isoformat()} UTC")


def initialise():
    """
    Call once at pipeline startup.
    Loads from DB if valid token exists, otherwise mints a new one.
    """
    global _cached_token, _expires_at
    with _lock:
        cached = _load_from_db()
        if cached:
            _cached_token, _expires_at = cached
            print(f"[token] Loaded existing token from DB (valid until {_expires_at.isoformat()} UTC)")
        else:
            _refresh()


def get_token() -> str:
    """
    Return a live Shopify access token.
    Automatically refreshes if within EXPIRY_BUFFER_SECONDS of expiry.
    Thread-safe.
    """
    global _cached_token, _expires_at
    with _lock:
        if datetime.utcnow() >= _expires_at - timedelta(seconds=EXPIRY_BUFFER_SECONDS):
            _refresh()
        return _cached_token


def get_headers() -> dict:
    """Return Shopify API headers with a live token."""
    return {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": get_token(),
    }


def smoke_test() -> bool:
    """
    Run a minimal Shopify query to confirm the token works.
    Returns True on success, raises on failure.
    """
    query = """{ shop { name plan { displayName } } }"""
    resp = requests.post(
        config.shopify_graphql_url,
        headers=get_headers(),
        json={"query": query},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    shop = data.get("data", {}).get("shop", {})
    if not shop:
        raise RuntimeError(f"Smoke test failed: {data}")
    print(f"[token] Smoke test passed. Store: {shop['name']} ({shop['plan']['displayName']})")
    return True


def start_background_refresh():
    """
    Start a daemon thread that checks token expiry every 10 minutes
    and refreshes proactively. Safe to call alongside the main pipeline.
    """
    def _loop():
        while True:
            time.sleep(600)
            try:
                get_token()  # This auto-refreshes if needed
            except Exception as e:
                print(f"[token] Background refresh error: {e}")

    t = threading.Thread(target=_loop, daemon=True, name="token-refresh")
    t.start()
    print("[token] Background token refresh thread started.")

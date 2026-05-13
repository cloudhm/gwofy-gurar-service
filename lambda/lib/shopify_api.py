"""Shopify OAuth (REST token exchange) + Admin GraphQL."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any
import requests

DEFAULT_API_VERSION = "2024-10"


def verify_oauth_hmac(query_params: dict[str, str], client_secret: str) -> bool:
    """Verify Shopify OAuth callback query HMAC."""
    pairs = []
    for k in sorted(query_params.keys()):
        if k == "hmac":
            continue
        pairs.append(f"{k}={query_params[k]}")
    message = "&".join(pairs).encode("utf-8")
    digest = hmac.new(client_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, query_params.get("hmac", ""))


def verify_webhook_hmac(raw_body: bytes, hmac_header: str, client_secret: str) -> bool:
    """Verify `X-Shopify-Hmac-Sha256` (Base64 of raw SHA256 HMAC digest, not hex)."""
    sig = (hmac_header or "").strip()
    if not sig:
        return False
    digest = hmac.new(client_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    try:
        received = base64.b64decode(sig, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(received) != len(digest):
        return False
    return hmac.compare_digest(digest, received)


def exchange_token(shop: str, client_id: str, client_secret: str, code: str) -> dict[str, Any]:
    """POST /admin/oauth/access_token — required REST endpoint."""
    url = f"https://{shop}/admin/oauth/access_token"
    r = requests.post(
        url,
        json={"client_id": client_id, "client_secret": client_secret, "code": code},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def graphql_request(
    shop: str,
    access_token: str,
    query: str,
    variables: dict[str, Any] | None = None,
    api_version: str = DEFAULT_API_VERSION,
    max_retries: int = 5,
) -> dict[str, Any]:
    url = f"https://{shop}/admin/api/{api_version}/graphql.json"
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }
    attempt = 0
    while True:
        attempt += 1
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code == 429:
            retry_after = float(r.headers.get("Retry-After", "2"))
            if attempt >= max_retries:
                r.raise_for_status()
            time.sleep(min(retry_after, 30))
            continue
        if r.status_code >= 500 and attempt < max_retries:
            time.sleep(min(2**attempt, 30))
            continue
        r.raise_for_status()
        return r.json()


def register_webhook_rest(
    shop: str,
    access_token: str,
    topic_path: str,
    callback_url: str,
    api_version: str = DEFAULT_API_VERSION,
) -> None:
    """Register webhook via Admin REST `webhooks.json` (topic e.g. `orders/create`)."""
    url = f"https://{shop}/admin/api/{api_version}/webhooks.json"
    r = requests.post(
        url,
        headers={"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"},
        json={"webhook": {"topic": topic_path, "address": callback_url, "format": "json"}},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"webhook register HTTP {r.status_code}: {r.text[:500]}")

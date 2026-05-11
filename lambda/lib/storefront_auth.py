"""HMAC verification for storefront / theme calls (no session JWT)."""

from __future__ import annotations

import hashlib
import hmac
import json


def verify_shop_body_hmac(secret: str, shop: str, raw_body: str, sig_hex: str) -> bool:
    """sig_hex = hex(hmac_sha256(secret, shop + '\\n' + raw_body))."""
    if not sig_hex or not secret:
        return False
    shop_n = shop.strip().lower().rstrip("/")
    msg = f"{shop_n}\n{raw_body}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, sig_hex.strip().lower())


def storefront_hmac_sign(secret: str, shop: str, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    msg = f"{shop.strip().lower().rstrip('/')}\n{body}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

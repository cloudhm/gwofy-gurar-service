"""Verify Shopify session token (JWT from App Bridge)."""

from __future__ import annotations

import os

import jwt


def verify_session_token(token: str, api_key: str, api_secret: str) -> dict:
    """
    Shopify embeds HS256 JWT signed with API secret (Partner Dashboard API credentials).
    See: https://shopify.dev/docs/apps/auth/session-tokens
    """
    return jwt.decode(
        token,
        api_secret,
        algorithms=["HS256"],
        audience=api_key,
        options={"require": ["exp"]},
    )


def shop_host_from_payload(payload: dict) -> str | None:
    dest = payload.get("dest") or ""
    if isinstance(dest, str) and "myshopify.com" in dest:
        # dest like https://shop.myshopify.com/admin
        parts = dest.replace("https://", "").replace("http://", "").split("/")
        return parts[0] if parts else None
    iss = payload.get("iss")
    if isinstance(iss, str) and "myshopify.com" in iss:
        return iss.split("//")[-1].split("/")[0]
    return None

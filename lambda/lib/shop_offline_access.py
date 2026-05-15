"""Resolve Shopify Admin offline tokens: expiring rotation + legacy non-expiring migration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .kms_tokens import decrypt_refresh_token, decrypt_token, encrypt_refresh_token, encrypt_token
from .models import SK_METADATA, pk_shop
from .shopify_api import migrate_non_expiring_offline_token, refresh_offline_access_token

logger = logging.getLogger(__name__)

ACCESS_EXPIRES_AT = "shopify_offline_access_token_expires_at"
REFRESH_EXPIRES_AT = "shopify_offline_refresh_token_expires_at"
REFRESH_ENC = "refresh_token_enc"

# Merchant must complete OAuth again; oauth_handler sets ACTIVE on reinstall.
INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED = "OFFLINE_AUTH_EXPIRED"

_ACCESS_SKEW = timedelta(seconds=120)
_REFRESH_SKEW = timedelta(seconds=120)


def _parse_iso_utc(val: Any) -> datetime | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _access_still_valid(access_exp: datetime | None, now: datetime) -> bool:
    if access_exp is None:
        return False
    return now < access_exp - _ACCESS_SKEW


def _refresh_still_valid(refresh_exp: datetime | None, now: datetime) -> bool:
    if refresh_exp is None:
        return True
    return now < refresh_exp - _REFRESH_SKEW


def mark_installation_offline_auth_expired(table: Any, shop: str) -> None:
    """Set installation_status when offline refresh is unusable (calendar or Shopify 401)."""
    shop_norm = shop.strip().lower().rstrip("/")
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression="SET installation_status = :s, updated_at = :u",
        ExpressionAttributeValues={
            ":s": INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED,
            ":u": now,
        },
    )
    logger.info(
        "installation_marked_offline_auth_expired",
        extra={"shop": shop_norm},
    )


def persist_expiring_offline_tokens(
    table: Any,
    shop: str,
    kms_key_id: str,
    token_resp: dict[str, Any],
) -> None:
    """Persist KMS-encrypted access + refresh and ISO expiries after OAuth code exchange, refresh, or migration."""
    shop_norm = shop.strip().lower().rstrip("/")
    now = datetime.now(timezone.utc)
    at = str(token_resp.get("access_token") or "")
    if not at:
        raise ValueError("token_response_missing_access_token")
    enc_a = encrypt_token(kms_key_id, at)
    expires_in = int(token_resp.get("expires_in") or 3600)
    access_exp = (now + timedelta(seconds=expires_in)).isoformat()
    rt = token_resp.get("refresh_token")
    if isinstance(rt, str) and rt.strip():
        enc_r = encrypt_refresh_token(kms_key_id, rt.strip())
        rt_in = int(token_resp.get("refresh_token_expires_in") or 7776000)
        refresh_exp = (now + timedelta(seconds=rt_in)).isoformat()
        table.update_item(
            Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
            UpdateExpression=(
                "SET access_token_enc = :a, updated_at = :u, "
                "shopify_offline_access_token_expires_at = :ae, "
                "refresh_token_enc = :r, shopify_offline_refresh_token_expires_at = :re"
            ),
            ExpressionAttributeValues={
                ":a": enc_a,
                ":u": now.isoformat(),
                ":ae": access_exp,
                ":r": enc_r,
                ":re": refresh_exp,
            },
        )
    else:
        table.update_item(
            Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
            UpdateExpression=(
                "SET access_token_enc = :a, updated_at = :u, "
                "shopify_offline_access_token_expires_at = :ae "
                "REMOVE refresh_token_enc, shopify_offline_refresh_token_expires_at"
            ),
            ExpressionAttributeValues={
                ":a": enc_a,
                ":u": now.isoformat(),
                ":ae": access_exp,
            },
        )


def get_fresh_shop_access_token(
    table: Any,
    shop: str,
    *,
    kms_key_id_fallback: str,
    client_id: str,
    client_secret: str,
    meta: dict[str, Any] | None = None,
) -> str:
    """Return a valid Admin API access token; refresh or migrate legacy token as needed."""
    shop_norm = shop.strip().lower().rstrip("/")
    row = meta
    if row is None:
        row = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    enc = row.get("access_token_enc")
    if not enc:
        raise ValueError("missing_access_token_enc")
    key_id = str(row.get("kms_key_id") or kms_key_id_fallback).strip() or kms_key_id_fallback

    access_plain = decrypt_token(key_id, str(enc))
    now = datetime.now(timezone.utc)
    access_exp = _parse_iso_utc(row.get(ACCESS_EXPIRES_AT))
    refresh_enc = row.get(REFRESH_ENC)
    refresh_exp = _parse_iso_utc(row.get(REFRESH_EXPIRES_AT))

    if refresh_enc:
        if not _refresh_still_valid(refresh_exp, now):
            mark_installation_offline_auth_expired(table, shop_norm)
            raise RuntimeError(
                "shopify_refresh_token_expired_merchant_must_reauthorize_app_in_shopify_admin"
            )
        if _access_still_valid(access_exp, now):
            return access_plain
        r_plain = decrypt_refresh_token(key_id, str(refresh_enc))
        logger.info("shopify_offline_token_refresh", extra={"shop": shop_norm})
        try:
            resp = refresh_offline_access_token(shop_norm, client_id, client_secret, r_plain)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                mark_installation_offline_auth_expired(table, shop_norm)
            raise
        persist_expiring_offline_tokens(table, shop_norm, key_id, resp)
        return str(resp.get("access_token") or "")

    if _access_still_valid(access_exp, now):
        return access_plain

    logger.info("shopify_offline_token_migrate_non_expiring", extra={"shop": shop_norm})
    try:
        resp = migrate_non_expiring_offline_token(shop_norm, client_id, client_secret, access_plain)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            mark_installation_offline_auth_expired(table, shop_norm)
        raise
    persist_expiring_offline_tokens(table, shop_norm, key_id, resp)
    return str(resp.get("access_token") or "")

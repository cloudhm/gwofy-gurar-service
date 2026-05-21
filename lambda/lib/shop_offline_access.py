"""Resolve Shopify Admin offline tokens: expiring rotation + legacy non-expiring migration."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from .kms_tokens import decrypt_refresh_token, decrypt_token, encrypt_refresh_token, encrypt_token
from .models import SK_METADATA, pk_shop
from .shopify_api import (
    admin_graphql_api_label,
    exchange_session_token_for_offline_access,
    graphql_request,
    migrate_non_expiring_offline_token,
    refresh_offline_access_token,
)

logger = logging.getLogger(__name__)

ACCESS_EXPIRES_AT = "shopify_offline_access_token_expires_at"
REFRESH_EXPIRES_AT = "shopify_offline_refresh_token_expires_at"
REFRESH_ENC = "refresh_token_enc"
LAST_OFFLINE_AUTH_401_API = "last_offline_auth_401_api"
LAST_OFFLINE_AUTH_401_AT = "last_offline_auth_401_at"
OFFLINE_AUTH_401_CONSECUTIVE_COUNT = "offline_auth_401_consecutive_count"

INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED = "OFFLINE_AUTH_EXPIRED"

# One bump = one failed auth round (OAuth refresh after retries, or GraphQL after force-refresh retry).
LOCK_AFTER_CONSECUTIVE_401 = 5

API_OAUTH_REFRESH_OFFLINE = "POST /admin/oauth/access_token (grant_type=refresh_token)"
API_OAUTH_MIGRATE_OFFLINE = (
    "POST /admin/oauth/access_token (grant_type=urn:ietf:params:oauth:grant-type:token-exchange)"
)
API_OAUTH_TOKEN_EXCHANGE_SESSION = (
    "POST /admin/oauth/access_token (grant_type=token-exchange, subject=id_token/session)"
)

_ACCESS_SKEW = timedelta(seconds=120)
_REFRESH_SKEW = timedelta(seconds=120)
_DEFAULT_REFRESH_RECOVERY_WINDOW_DAYS = 7
_OAUTH_401_ATTEMPTS = 2
_OAUTH_401_RETRY_SLEEP_SEC = 1.0


class ShopifyAuth401Error(Exception):
    """Shopify rejected credentials (HTTP 401)."""

    def __init__(self, api_name: str, *, requires_reauthorize: bool) -> None:
        self.api_name = api_name
        self.requires_reauthorize = requires_reauthorize
        super().__init__(f"shopify_auth_401:{api_name}")


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


def _requires_reauthorize(refresh_exp: datetime | None, now: datetime) -> bool:
    return not _refresh_still_valid(refresh_exp, now)


def _session_recovery_refresh_window() -> timedelta:
    raw = (os.environ.get("OFFLINE_REFRESH_RECOVERY_WINDOW_DAYS") or "").strip()
    try:
        days = int(raw) if raw else _DEFAULT_REFRESH_RECOVERY_WINDOW_DAYS
    except ValueError:
        days = _DEFAULT_REFRESH_RECOVERY_WINDOW_DAYS
    return timedelta(days=max(1, days))


def offline_token_recovery_reason(meta: dict[str, Any], now: datetime | None = None) -> str | None:
    """
    Why session-token exchange should run, or None if not needed.

    Reasons: offline_auth_expired | missing_refresh | refresh_expired | refresh_near_expiry
    """
    if not meta.get("access_token_enc"):
        return None
    now = now or datetime.now(timezone.utc)
    if meta.get("installation_status") == INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED:
        return "offline_auth_expired"
    if not meta.get(REFRESH_ENC):
        return "missing_refresh"
    refresh_exp = _parse_iso_utc(meta.get(REFRESH_EXPIRES_AT))
    if refresh_exp is None:
        return None
    if not _refresh_still_valid(refresh_exp, now):
        return "refresh_expired"
    window = _session_recovery_refresh_window()
    if now >= refresh_exp - window:
        return "refresh_near_expiry"
    return None


def offline_token_needs_session_recovery(meta: dict[str, Any], now: datetime | None = None) -> bool:
    return offline_token_recovery_reason(meta, now) is not None


def recover_offline_token_from_session(
    table: Any,
    shop: str,
    session_token: str,
    *,
    kms_key_id: str,
    client_id: str,
    client_secret: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Exchange embedded-app session JWT for a new expiring offline token pair and persist.

    Restores installation_status ACTIVE when refresh calendar on the new pair is valid.
    """
    shop_norm = shop.strip().lower().rstrip("/")
    row = meta
    if row is None:
        row = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    key_id = str(row.get("kms_key_id") or kms_key_id).strip() or kms_key_id
    reason = offline_token_recovery_reason(row)
    if not reason:
        return row

    logger.info(
        "shopify_offline_token_session_recovery_start",
        extra={"shop": shop_norm, "reason": reason},
    )

    try:
        resp = exchange_session_token_for_offline_access(
            shop_norm, client_id, client_secret, session_token
        )
    except requests.HTTPError as e:
        logger.warning(
            "shopify_offline_token_session_recovery_failed",
            extra={
                "shop": shop_norm,
                "reason": reason,
                "status": e.response.status_code if e.response is not None else None,
                "body_preview": (e.response.text or "")[:400] if e.response is not None else "",
            },
        )
        raise ShopifyAuth401Error(
            API_OAUTH_TOKEN_EXCHANGE_SESSION,
            requires_reauthorize=True,
        ) from e

    persist_expiring_offline_tokens(table, shop_norm, key_id, resp)
    scope = resp.get("scope")
    if isinstance(scope, str) and scope.strip():
        table.update_item(
            Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
            UpdateExpression="SET scopes = :s, updated_at = :u",
            ExpressionAttributeValues={
                ":s": scope.strip(),
                ":u": datetime.now(timezone.utc).isoformat(),
            },
        )

    logger.info(
        "shopify_offline_token_session_recovery_ok",
        extra={"shop": shop_norm, "reason": reason},
    )
    return table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or row


def mark_installation_offline_auth_expired(table: Any, shop: str) -> None:
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
    logger.info("installation_marked_offline_auth_expired", extra={"shop": shop_norm})


def _restore_installation_active_if_refresh_valid(
    table: Any, shop: str, refresh_exp: datetime | None, now: datetime
) -> None:
    if not _refresh_still_valid(refresh_exp, now):
        return
    shop_norm = shop.strip().lower().rstrip("/")
    now_iso = now.isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression="SET installation_status = :s, updated_at = :u",
        ExpressionAttributeValues={":s": "ACTIVE", ":u": now_iso},
    )
    logger.info("installation_restored_active_after_token_refresh", extra={"shop": shop_norm})


def clear_offline_auth_401_failures(table: Any, shop: str) -> None:
    """Reset consecutive 401 counter after a successful token refresh."""
    shop_norm = shop.strip().lower().rstrip("/")
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression=(
            f"REMOVE {OFFLINE_AUTH_401_CONSECUTIVE_COUNT}, "
            f"{LAST_OFFLINE_AUTH_401_API}, {LAST_OFFLINE_AUTH_401_AT} "
            "SET updated_at = :u"
        ),
        ExpressionAttributeValues={":u": now},
    )


def bump_offline_auth_401_failure(
    table: Any,
    shop: str,
    api_name: str,
    refresh_exp: datetime | None,
    now: datetime,
) -> tuple[int, bool]:
    """
    Record one failed auth round and increment consecutive 401 counter.

    Locks the shop when refresh calendar is expired OR count reaches LOCK_AFTER_CONSECUTIVE_401.
    Returns (new_count, installation_locked).
    """
    shop_norm = shop.strip().lower().rstrip("/")
    now_iso = now.isoformat()
    resp = table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression=(
            f"ADD {OFFLINE_AUTH_401_CONSECUTIVE_COUNT} :one "
            f"SET {LAST_OFFLINE_AUTH_401_API} = :api, {LAST_OFFLINE_AUTH_401_AT} = :at, updated_at = :u"
        ),
        ExpressionAttributeValues={
            ":one": 1,
            ":api": api_name[:500],
            ":at": now_iso,
            ":u": now_iso,
        },
        ReturnValues="UPDATED_NEW",
    )
    count = int((resp.get("Attributes") or {}).get(OFFLINE_AUTH_401_CONSECUTIVE_COUNT) or 1)
    lock = _requires_reauthorize(refresh_exp, now) or count >= LOCK_AFTER_CONSECUTIVE_401
    if lock:
        mark_installation_offline_auth_expired(table, shop_norm)
    logger.info(
        "offline_auth_401_failure_bumped",
        extra={
            "shop": shop_norm,
            "api": api_name,
            "consecutive_count": count,
            "lock_threshold": LOCK_AFTER_CONSECUTIVE_401,
            "installation_locked": lock,
        },
    )
    return count, lock


def _handle_token_endpoint_401(
    table: Any,
    shop: str,
    api_name: str,
    refresh_exp: datetime | None,
    now: datetime,
) -> ShopifyAuth401Error:
    _count, locked = bump_offline_auth_401_failure(table, shop, api_name, refresh_exp, now)
    return ShopifyAuth401Error(api_name, requires_reauthorize=locked)


def _oauth_call_with_401_handling(
    table: Any,
    shop: str,
    api_name: str,
    refresh_exp: datetime | None,
    now: datetime,
    call: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    last_err: requests.HTTPError | None = None
    for attempt in range(_OAUTH_401_ATTEMPTS):
        try:
            return call()
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 401:
                raise
            last_err = e
            if attempt + 1 < _OAUTH_401_ATTEMPTS:
                time.sleep(_OAUTH_401_RETRY_SLEEP_SEC)
                continue
            raise _handle_token_endpoint_401(table, shop, api_name, refresh_exp, now) from e
    if last_err is not None:
        raise last_err
    raise RuntimeError("oauth_call_unexpected")


def persist_expiring_offline_tokens(
    table: Any,
    shop: str,
    kms_key_id: str,
    token_resp: dict[str, Any],
) -> None:
    shop_norm = shop.strip().lower().rstrip("/")
    now = datetime.now(timezone.utc)
    at = str(token_resp.get("access_token") or "")
    if not at:
        raise ValueError("token_response_missing_access_token")
    enc_a = encrypt_token(kms_key_id, at)
    expires_in = int(token_resp.get("expires_in") or 3600)
    access_exp = (now + timedelta(seconds=expires_in)).isoformat()
    rt = token_resp.get("refresh_token")
    refresh_exp_dt: datetime | None = None
    if isinstance(rt, str) and rt.strip():
        enc_r = encrypt_refresh_token(kms_key_id, rt.strip())
        rt_in = int(token_resp.get("refresh_token_expires_in") or 7776000)
        refresh_exp = (now + timedelta(seconds=rt_in)).isoformat()
        refresh_exp_dt = _parse_iso_utc(refresh_exp)
        table.update_item(
            Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
            UpdateExpression=(
                "SET access_token_enc = :a, updated_at = :u, "
                "shopify_offline_access_token_expires_at = :ae, "
                "refresh_token_enc = :r, shopify_offline_refresh_token_expires_at = :re, "
                "installation_status = :active "
                f"REMOVE {OFFLINE_AUTH_401_CONSECUTIVE_COUNT}, "
                f"{LAST_OFFLINE_AUTH_401_API}, {LAST_OFFLINE_AUTH_401_AT}"
            ),
            ExpressionAttributeValues={
                ":a": enc_a,
                ":u": now.isoformat(),
                ":ae": access_exp,
                ":r": enc_r,
                ":re": refresh_exp,
                ":active": "ACTIVE",
            },
        )
    else:
        table.update_item(
            Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
            UpdateExpression=(
                "SET access_token_enc = :a, updated_at = :u, "
                "shopify_offline_access_token_expires_at = :ae, installation_status = :active "
                "REMOVE refresh_token_enc, shopify_offline_refresh_token_expires_at, "
                f"{OFFLINE_AUTH_401_CONSECUTIVE_COUNT}, "
                f"{LAST_OFFLINE_AUTH_401_API}, {LAST_OFFLINE_AUTH_401_AT}"
            ),
            ExpressionAttributeValues={
                ":a": enc_a,
                ":u": now.isoformat(),
                ":ae": access_exp,
                ":active": "ACTIVE",
            },
        )
    _restore_installation_active_if_refresh_valid(table, shop_norm, refresh_exp_dt, now)


def get_fresh_shop_access_token(
    table: Any,
    shop: str,
    *,
    kms_key_id_fallback: str,
    client_id: str,
    client_secret: str,
    meta: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> str:
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
            raise ShopifyAuth401Error(
                API_OAUTH_REFRESH_OFFLINE,
                requires_reauthorize=True,
            )
        if _access_still_valid(access_exp, now) and not force_refresh:
            return access_plain
        r_plain = decrypt_refresh_token(key_id, str(refresh_enc))
        logger.info(
            "shopify_offline_token_refresh",
            extra={"shop": shop_norm, "force_refresh": force_refresh},
        )

        def _do_refresh() -> dict[str, Any]:
            return refresh_offline_access_token(shop_norm, client_id, client_secret, r_plain)

        resp = _oauth_call_with_401_handling(
            table, shop_norm, API_OAUTH_REFRESH_OFFLINE, refresh_exp, now, _do_refresh
        )
        persist_expiring_offline_tokens(table, shop_norm, key_id, resp)
        return str(resp.get("access_token") or "")

    if _access_still_valid(access_exp, now) and not force_refresh:
        return access_plain

    logger.info("shopify_offline_token_migrate_non_expiring", extra={"shop": shop_norm})

    def _do_migrate() -> dict[str, Any]:
        return migrate_non_expiring_offline_token(shop_norm, client_id, client_secret, access_plain)

    resp = _oauth_call_with_401_handling(
        table, shop_norm, API_OAUTH_MIGRATE_OFFLINE, refresh_exp, now, _do_migrate
    )
    persist_expiring_offline_tokens(table, shop_norm, key_id, resp)
    return str(resp.get("access_token") or "")


def retry_shop_offline_token_refresh(
    table: Any,
    shop: str,
    *,
    kms_key_id_fallback: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Force offline token refresh (ops / admin). Restores ACTIVE when refresh calendar is valid."""
    shop_norm = shop.strip().lower().rstrip("/")
    row = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    token = get_fresh_shop_access_token(
        table,
        shop_norm,
        kms_key_id_fallback=kms_key_id_fallback,
        client_id=client_id,
        client_secret=client_secret,
        meta=row,
        force_refresh=True,
    )
    row2 = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    return {
        "ok": True,
        "access_token_preview": token[:8] + "…" if len(token) > 8 else "(short)",
        "installation_status": row2.get("installation_status"),
        "shopify_offline_access_token_expires_at": row2.get(ACCESS_EXPIRES_AT),
        "shopify_offline_refresh_token_expires_at": row2.get(REFRESH_EXPIRES_AT),
        "last_offline_auth_401_api": row2.get(LAST_OFFLINE_AUTH_401_API),
        "last_offline_auth_401_at": row2.get(LAST_OFFLINE_AUTH_401_AT),
        "offline_auth_401_consecutive_count": row2.get(OFFLINE_AUTH_401_CONSECUTIVE_COUNT),
        "offline_auth_401_lock_threshold": LOCK_AFTER_CONSECUTIVE_401,
    }


@dataclass
class ShopAdminAuth:
    """Per-shop Admin API auth: token refresh + GraphQL with 401 retry."""

    table: Any
    shop: str
    kms_key_id: str
    client_id: str
    client_secret: str
    api_version: str
    _meta: dict[str, Any] | None = field(default=None, repr=False)

    def load_meta(self) -> dict[str, Any]:
        if self._meta is None:
            self._meta = (
                self.table.get_item(
                    Key={"pk": pk_shop(self.shop), "sk": SK_METADATA}
                ).get("Item")
                or {}
            )
        return self._meta

    def invalidate_meta(self) -> None:
        self._meta = None

    def access_token(self, *, force_refresh: bool = False) -> str:
        return get_fresh_shop_access_token(
            self.table,
            self.shop,
            kms_key_id_fallback=self.kms_key_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
            meta=self.load_meta(),
            force_refresh=force_refresh,
        )

    def record_401(self, api_name: str) -> None:
        row = self.load_meta()
        now = datetime.now(timezone.utc)
        refresh_exp = _parse_iso_utc(row.get(REFRESH_EXPIRES_AT))
        bump_offline_auth_401_failure(self.table, self.shop, api_name, refresh_exp, now)

    def graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        operation: str | None = None,
    ) -> dict[str, Any]:
        label = admin_graphql_api_label(self.api_version, operation)
        try:
            return graphql_request(
                self.shop,
                self.access_token(),
                query,
                variables,
                self.api_version,
                access_token_refresh=self._refresh_token_for_graphql_401,
                record_401=self.record_401,
                api_operation=label,
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                # record_401 callback (final GraphQL 401) already bumped the counter.
                self.invalidate_meta()
                row = self.load_meta()
                locked = row.get("installation_status") == INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED
                raise ShopifyAuth401Error(label, requires_reauthorize=locked) from e
            raise

    def _refresh_token_for_graphql_401(self) -> str:
        self.invalidate_meta()
        return self.access_token(force_refresh=True)


def shop_admin_graphql_call(
    shop: str,
    token: str,
    query: str,
    variables: dict[str, Any] | None,
    api_version: str,
    *,
    auth: ShopAdminAuth | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    """GraphQL with optional ShopAdminAuth (401 → force refresh + one retry)."""
    if auth is not None:
        return auth.graphql(query, variables, operation=operation)
    return graphql_request(shop, token, query, variables, api_version)


def shopify_auth_401_response_body(err: ShopifyAuth401Error, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": "shopify_offline_auth_failed",
        "api": err.api_name,
        "requires_reauthorize": err.requires_reauthorize,
        "offline_auth_401_lock_threshold": LOCK_AFTER_CONSECUTIVE_401,
    }
    if meta:
        body["last_offline_auth_401_api"] = meta.get(LAST_OFFLINE_AUTH_401_API)
        body["last_offline_auth_401_at"] = meta.get(LAST_OFFLINE_AUTH_401_AT)
        body["offline_auth_401_consecutive_count"] = meta.get(OFFLINE_AUTH_401_CONSECUTIVE_COUNT)
        body["installation_status"] = meta.get("installation_status")
    return body

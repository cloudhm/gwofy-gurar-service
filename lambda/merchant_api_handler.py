"""Merchant Session API: /api/me, install, activate, embed ack, storefront cart-config (HMAC)."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone

import boto3
import requests

from lib.activate_app import ActivateAppError, run_activate_app_safe
from lib.audit import append_audit
from lib.cart_config_response import build_cart_plugin_response
from lib.shipping_country_defaults import is_country_supported
from lib.logging_json import setup_logging
from lib.merchant_app_config import (
    ALLOWED_KEYS as APP_CONFIG_ALLOWED_KEYS,
    merge_app_config,
    normalize_for_storage as normalize_app_config_for_storage,
    parse_app_config_from_meta,
    should_remove_storage,
    validate_patch as validate_app_config_patch,
)
from lib.merchant_premium_rules import normalize_for_storage, parse_rules_from_meta, validate_rules
from lib.embed_deep_link import build_embed_deep_link, resolve_main_theme_gid
from lib.theme_sync import fetch_main_theme_gid, update_main_theme_gid_metadata
from lib.models import MERCHANT_APP_CONFIG_JSON, MERCHANT_PREMIUM_RULES_JSON, SK_METADATA, pk_shop
from lib.session_jwt import shop_host_from_payload, verify_session_token
from lib.shop_enabled_currencies import parse_shop_enabled_currencies_json, sync_shop_enabled_currencies
from lib.static_assets import (
    APP_CONFIG_VERSION,
    APP_STOREFRONT_VERSION,
    get_app_config_js_for_shop,
    get_app_storefront_asset,
)
from lib.storefront_gwofy_config import (
    build_effective_gwofy_config,
    is_valid_shop_host,
    shop_query_from_event,
)
from lib.storefront_auth import verify_shop_body_hmac
from lib.shop_install import (
    enqueue_install_worker_jobs,
    shop_needs_install_bootstrap,
    upsert_shop_metadata_from_offline_tokens,
)
from lib.shop_offline_access import (
    LOCK_AFTER_CONSECUTIVE_401,
    ShopAdminAuth,
    ShopifyAuth401Error,
    get_fresh_shop_access_token,
    offline_token_recovery_reason,
    persist_expiring_offline_tokens,
    recover_offline_token_from_session,
    shopify_auth_401_response_body,
)
from lib.shopify_api import (
    DEFAULT_API_VERSION,
    exchange_session_token_for_offline_access,
)

logger = setup_logging("merchant_api")

ddb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")


def _http_ctx(event):
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    return http.get("method", "GET").upper(), http.get("path", ""), rc.get("requestId", "")


def _headers(event) -> dict[str, str]:
    return {k.lower(): v for k, v in (event.get("headers") or {}).items()}


def _xff(h: dict[str, str]) -> str | None:
    x = h.get("x-forwarded-for") or ""
    return x.split(",")[0].strip() or None


def handler(event, context):
    method, path, req_id = _http_ctx(event)
    headers = _headers(event)
    table_name = os.environ["TABLE_NAME"]
    table = ddb.Table(table_name)

    if method in ("GET", "HEAD") and path == "/static/app-storefront.js":
        return _serve_app_storefront_js(method, headers)

    if method in ("GET", "HEAD") and path == "/static/app-config.js":
        return _serve_app_config_js(event, method, headers, table)

    if method == "POST" and path == "/api/cart-config":
        return _cart_config(event, table, req_id)

    auth = headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        return _resp(401, {"error": "missing_bearer"})
    token = auth.split(" ", 1)[1].strip()
    api_key = os.environ["SHOPIFY_CLIENT_ID"]
    api_secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    try:
        payload = verify_session_token(token, api_key, api_secret)
    except Exception as e:
        logger.info("session_token_invalid", extra={"detail": str(e)[:120]})
        return _resp(401, {"error": "invalid_session_token"})

    shop_host = shop_host_from_payload(payload)
    if not shop_host:
        return _resp(400, {"error": "cannot_resolve_shop"})
    shop_host = shop_host.strip().lower().rstrip("/")

    recovery_resp = _try_recover_offline_token_from_session(
        table, shop_host, token, payload, headers, req_id
    )
    if recovery_resp is not None:
        return recovery_resp

    if method == "GET" and path == "/api/me":
        return _api_me(table, shop_host, payload, headers, req_id)
    if method == "POST" and path == "/api/install":
        return _api_install(
            table, shop_host, token, payload, headers, req_id
        )
    if method == "POST" and path == "/api/activate":
        return _activate(table, shop_host, payload, headers, req_id)
    if method == "PATCH" and path == "/api/me/embed":
        return _embed_ack(event, table, shop_host, payload, headers, req_id)
    if method == "POST" and path == "/api/shop-enabled-currencies/sync":
        return _sync_shop_currencies(table, shop_host, payload, headers, req_id)
    if method == "GET" and path == "/api/me/merchant-premium-rules":
        return _get_merchant_premium_rules(table, shop_host, payload, headers, req_id)
    if method == "PUT" and path == "/api/me/merchant-premium-rules":
        return _put_merchant_premium_rules(event, table, shop_host, payload, headers, req_id)
    if method == "GET" and path == "/api/me/app-config":
        return _get_merchant_app_config(table, shop_host, payload, headers, req_id)
    if method == "PATCH" and path == "/api/me/app-config":
        return _patch_merchant_app_config(event, table, shop_host, payload, headers, req_id)

    return _resp(404, {"error": "not_found"})


def _try_recover_offline_token_from_session(
    table,
    shop_host: str,
    session_token: str,
    payload: dict,
    headers: dict,
    req_id: str,
):
    """When offline auth is expired or refresh is missing/near expiry, exchange session JWT for new offline pair."""
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    if not item:
        return None
    reason = offline_token_recovery_reason(item)
    if not reason:
        return None

    actor_sub = str(payload.get("sub") or "")
    kms_key_id = os.environ["KMS_KEY_ID"]
    client_id = os.environ["SHOPIFY_CLIENT_ID"]
    client_secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    critical = reason in ("offline_auth_expired", "missing_refresh", "refresh_expired")

    try:
        recover_offline_token_from_session(
            table,
            shop_host,
            session_token,
            kms_key_id=kms_key_id,
            client_id=client_id,
            client_secret=client_secret,
            meta=item,
        )
        append_audit(
            table,
            shop_host,
            actor_type="merchant",
            actor_id=actor_sub,
            action="OFFLINE_TOKEN_SESSION_RECOVERY",
            outcome="ok",
            detail={"reason": reason},
            http_path="/api/session-recovery",
            request_id=req_id,
            source_ip=_xff(headers),
        )
        return None
    except ShopifyAuth401Error as e:
        append_audit(
            table,
            shop_host,
            actor_type="merchant",
            actor_id=actor_sub,
            action="OFFLINE_TOKEN_SESSION_RECOVERY",
            outcome="failed",
            detail={"reason": reason, "api": e.api_name},
            http_path="/api/session-recovery",
            request_id=req_id,
            source_ip=_xff(headers),
        )
        row = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item") or item
        return _resp(401, shopify_auth_401_response_body(e, row))
    except Exception as e:
        logger.exception(
            "offline_token_session_recovery_error",
            extra={"shop": shop_host, "reason": reason},
        )
        append_audit(
            table,
            shop_host,
            actor_type="merchant",
            actor_id=actor_sub,
            action="OFFLINE_TOKEN_SESSION_RECOVERY",
            outcome="failed",
            detail={"reason": reason, "detail": str(e)[:200]},
            http_path="/api/session-recovery",
            request_id=req_id,
            source_ip=_xff(headers),
        )
        if critical:
            return _resp(
                502,
                {
                    "error": "offline_token_recovery_failed",
                    "reason": reason,
                    "detail": str(e)[:400],
                },
            )
        return None


def _api_install(
    table,
    shop_host: str,
    session_token: str,
    payload: dict,
    headers: dict,
    req_id: str,
):
    """
    Embedded app install: session JWT → offline token pair, persist METADATA, enqueue INITIAL_SYNC.

    Mirrors ``/oauth/callback`` for shops that open the app before OAuth redirect completes.
    """
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    if item and item.get("plugin_suspended"):
        return _resp(403, {"error": "plugin_suspended"})

    kms_key_id = os.environ["KMS_KEY_ID"]
    client_id = os.environ["SHOPIFY_CLIENT_ID"]
    client_secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    table_name = os.environ["TABLE_NAME"]
    queue_url = os.environ.get("WORK_QUEUE_URL", "").strip()
    actor_sub = str(payload.get("sub") or "")

    try:
        token_resp = exchange_session_token_for_offline_access(
            shop_host, client_id, client_secret, session_token
        )
    except requests.HTTPError:
        logger.warning("session_install_token_exchange_failed", extra={"shop": shop_host})
        append_audit(
            table,
            shop_host,
            actor_type="merchant",
            actor_id=actor_sub,
            action="INSTALL_SESSION",
            outcome="failed",
            detail={"stage": "token_exchange"},
            http_path="/api/install",
            request_id=req_id,
            source_ip=_xff(headers),
        )
        return _resp(
            401,
            {
                "error": "session_token_exchange_failed",
                "hint": "Re-open the app from Shopify Admin so App Bridge issues a fresh session token.",
            },
        )
    except Exception as e:
        logger.exception("session_install_token_exchange_error", extra={"shop": shop_host})
        return _resp(
            502,
            {"error": "session_token_exchange_failed", "detail": str(e)[:400]},
        )

    needs_bootstrap = shop_needs_install_bootstrap(item)
    if needs_bootstrap:
        if not queue_url:
            return _resp(500, {"error": "work_queue_not_configured"})
        store_number = upsert_shop_metadata_from_offline_tokens(
            table,
            table_name,
            shop_host,
            token_resp,
            kms_key_id,
        )
        enqueue_install_worker_jobs(
            queue_url=queue_url,
            shop=shop_host,
            store_number=store_number,
            api_version=api_version,
            source="merchant_api",
        )
    else:
        persist_expiring_offline_tokens(table, shop_host, kms_key_id, token_resp)
        store_number = str(item.get("store_number") or "")

    row = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item") or {}
    append_audit(
        table,
        shop_host,
        actor_type="merchant",
        actor_id=actor_sub,
        action="INSTALL_SESSION",
        outcome="ok",
        detail={"bootstrap_enqueued": needs_bootstrap, "store_number": store_number},
        http_path="/api/install",
        request_id=req_id,
        source_ip=_xff(headers),
    )
    return _resp(
        200,
        {
            "ok": True,
            "shop": shop_host,
            "store_number": store_number,
            "installation_status": row.get("installation_status", "ACTIVE"),
            "activation_status": row.get("activation_status", "UNACTIVATED"),
            "bootstrap_enqueued": needs_bootstrap,
        },
    )


def _api_me(table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    embed_url = _embed_deep_link_for_shop(table, shop_host, item)
    if not item:
        safe = {
            "shop": shop_host,
            "installation_status": "NOT_INSTALLED",
            "activation_status": "UNACTIVATED",
            "return_insurance_status": "CLOSED",
            "shipping_protection_status": "CLOSED",
            "plugin_suspended": False,
            "embed_enabled_ack": False,
            "embed_deep_link": embed_url,
            "shop_enabled_currencies": [],
        }
        return _resp(200, {"session": payload.get("sub"), "shop_metadata": safe})
    if item.get("plugin_suspended"):
        append_audit(
            table,
            shop_host,
            actor_type="merchant",
            actor_id=str(payload.get("sub") or ""),
            action="API_ME",
            outcome="blocked",
            http_path="/api/me",
            request_id=req_id,
            source_ip=_xff(headers),
        )
        return _resp(403, {"error": "plugin_suspended"})

    safe = {
        "shop": item.get("shop"),
        "store_number": item.get("store_number"),
        "auth_id": item.get("store_number"),
        "installation_status": item.get("installation_status"),
        "activation_status": item.get("activation_status") or "UNACTIVATED",
        "return_insurance_status": item.get("return_insurance_status") or "CLOSED",
        "shipping_protection_status": item.get("shipping_protection_status") or "CLOSED",
        "plugin_suspended": bool(item.get("plugin_suspended")),
        "protection_product_gid": item.get("protection_product_gid"),
        "embed_enabled_ack": bool(item.get("embed_enabled_ack")),
        "embed_deep_link": embed_url,
        "shop_currency_code": item.get("shop_currency_code"),
        "shop_profile_synced_at": item.get("shop_profile_synced_at"),
        "shop_name": item.get("shop_name"),
        "scopes": item.get("scopes"),
        "installed_at": item.get("installed_at"),
        "updated_at": item.get("updated_at"),
        "last_fx_usd_to_shop": item.get("last_fx_usd_to_shop"),
        "last_fx_as_of": item.get("last_fx_as_of"),
        "last_activation_error": item.get("last_activation_error"),
        "last_offline_auth_401_api": item.get("last_offline_auth_401_api"),
        "last_offline_auth_401_at": item.get("last_offline_auth_401_at"),
        "offline_auth_401_consecutive_count": item.get("offline_auth_401_consecutive_count"),
        "offline_auth_401_lock_threshold": LOCK_AFTER_CONSECUTIVE_401,
        "shop_enabled_currencies": sorted(parse_shop_enabled_currencies_json(item)),
        "shop_enabled_currencies_synced_at": item.get("shop_enabled_currencies_synced_at"),
    }

    _maybe_enqueue_profile_refresh(table, shop_host, item)

    return _resp(200, {"session": payload.get("sub"), "shop_metadata": safe})


def _embed_deep_link_for_shop(table, shop_host: str, item: dict | None) -> str:
    theme_gid = resolve_main_theme_gid(item, table, shop_host)
    if not theme_gid and item and item.get("access_token_enc"):
        try:
            api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
            auth = ShopAdminAuth(
                table,
                shop_host,
                os.environ["KMS_KEY_ID"],
                os.environ["SHOPIFY_CLIENT_ID"],
                os.environ["SHOPIFY_CLIENT_SECRET"],
                api_version,
                _meta=item,
            )
            theme_gid = fetch_main_theme_gid(shop_host, auth.access_token(), api_version, auth=auth)
            if theme_gid:
                update_main_theme_gid_metadata(table, shop_host, theme_gid)
        except Exception as e:
            logger.warning(
                "embed_theme_gid_fetch_failed",
                extra={"shop": shop_host, "detail": str(e)[:200]},
            )
    if (
        not theme_gid
        and item
        and item.get("installation_status") == "ACTIVE"
        and not item.get("themes_synced_at")
    ):
        _send_sqs_theme_sync(shop_host, str(item.get("store_number", "")))
    meta_for_embed = dict(item) if item else {}
    if theme_gid:
        meta_for_embed["main_theme_gid"] = theme_gid
    return build_embed_deep_link(shop_host, meta_for_embed, table)


def _maybe_enqueue_profile_refresh(table, shop_host: str, item: dict) -> None:
    synced = item.get("shop_profile_synced_at")
    if not synced:
        _send_sqs_profile(shop_host, str(item.get("store_number", "")))
        return
    try:
        t = datetime.fromisoformat(synced.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - t > timedelta(hours=24):
            _send_sqs_profile(shop_host, str(item.get("store_number", "")))
    except ValueError:
        _send_sqs_profile(shop_host, str(item.get("store_number", "")))


def _send_sqs_profile(shop: str, store_number: str) -> None:
    q = os.environ.get("WORK_QUEUE_URL")
    if not q:
        return
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    sqs.send_message(
        QueueUrl=q,
        MessageBody=json.dumps(
            {
                "source": "merchant_api",
                "event": "SHOP_PROFILE_SYNC",
                "shop": shop,
                "store_number": store_number,
                "api_version": api_version,
            }
        ),
    )


def _send_sqs_theme_sync(shop: str, store_number: str) -> None:
    q = os.environ.get("WORK_QUEUE_URL")
    if not q:
        return
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    sqs.send_message(
        QueueUrl=q,
        MessageBody=json.dumps(
            {
                "source": "merchant_api",
                "event": "THEME_SYNC",
                "shop": shop,
                "store_number": store_number,
                "api_version": api_version,
            }
        ),
    )


def _activate(table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    if not item:
        return _resp(404, {"error": "shop_not_installed"})
    if item.get("installation_status") != "ACTIVE":
        return _resp(400, {"error": "shop_not_active"})
    if item.get("plugin_suspended"):
        return _resp(403, {"error": "plugin_suspended"})

    append_audit(
        table,
        shop_host,
        actor_type="merchant",
        actor_id=str(payload.get("sub") or ""),
        action="ACTIVATE_REQUEST",
        outcome="ok",
        http_path="/api/activate",
        request_id=req_id,
        source_ip=_xff(headers),
    )

    enc = item.get("access_token_enc")
    if not enc:
        return _resp(400, {"error": "missing_access_token"})
    kms_key_id = os.environ["KMS_KEY_ID"]
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    auth = ShopAdminAuth(
        table,
        shop_host,
        kms_key_id,
        os.environ["SHOPIFY_CLIENT_ID"],
        os.environ["SHOPIFY_CLIENT_SECRET"],
        api_version,
        _meta=item,
    )
    try:
        shop_token = auth.access_token()
    except ShopifyAuth401Error as e:
        return _resp(401, shopify_auth_401_response_body(e, item))
    except Exception as e:
        logger.exception("activate_token_resolve_failed", extra={"shop": shop_host})
        return _resp(500, {"error": "token_resolve_failed", "detail": str(e)[:400]})

    actor_sub = str(payload.get("sub") or "")
    try:
        run_activate_app_safe(
            table,
            shop_host,
            str(item.get("store_number", "")),
            shop_token,
            kms_key_id,
            api_version,
            actor_sub=actor_sub,
            auth=auth,
        )
    except ShopifyAuth401Error as e:
        return _resp(401, shopify_auth_401_response_body(e, item))
    except ActivateAppError as e:
        body: dict = {"error": e.code, "message": e.message}
        if e.currency:
            body["currency"] = e.currency
        if e.supported is not None:
            body["supported"] = e.supported
        return _resp(400, body)
    except Exception as e:
        logger.exception("activate_failed", extra={"shop": shop_host})
        return _resp(
            502,
            {"error": "activate_upstream_failed", "detail": str(e)[:500]},
        )

    return _resp(
        200,
        {"ok": True, "activation_status": "ACTIVATED"},
    )


def _embed_ack(event, table, shop_host: str, payload: dict, headers: dict, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    ack = body.get("embed_enabled_ack")
    if not isinstance(ack, bool):
        return _resp(400, {"error": "embed_enabled_ack_boolean_required"})
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_host), "sk": SK_METADATA},
        UpdateExpression="SET embed_enabled_ack = :a, updated_at = :u",
        ExpressionAttributeValues={":a": ack, ":u": now},
    )
    append_audit(
        table,
        shop_host,
        actor_type="merchant",
        actor_id=str(payload.get("sub") or ""),
        action="EMBED_ACK",
        outcome="ok",
        detail={"embed_enabled_ack": ack},
        http_path="/api/me/embed",
        request_id=req_id,
        source_ip=_xff(headers),
    )
    return _resp(200, {"ok": True})


def _require_merchant_shop_active(
    table, shop_host: str, payload: dict, headers: dict, req_id: str, *, http_path: str, audit_action: str
):
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    if not item:
        return None, _resp(404, {"error": "shop_not_installed"})
    if item.get("installation_status") != "ACTIVE":
        return None, _resp(400, {"error": "shop_not_active"})
    if item.get("plugin_suspended"):
        append_audit(
            table,
            shop_host,
            actor_type="merchant",
            actor_id=str(payload.get("sub") or ""),
            action=audit_action,
            outcome="blocked",
            http_path=http_path,
            request_id=req_id,
            source_ip=_xff(headers),
        )
        return None, _resp(403, {"error": "plugin_suspended"})
    return item, None


def _get_merchant_premium_rules(table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item, err = _require_merchant_shop_active(
        table,
        shop_host,
        payload,
        headers,
        req_id,
        http_path="/api/me/merchant-premium-rules",
        audit_action="MERCHANT_PREMIUM_RULES_GET",
    )
    if err:
        return err
    rules, _warn = parse_rules_from_meta(table, item)
    return _resp(200, {"merchantPremiumRules": rules})


def _put_merchant_premium_rules(event, table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item, err = _require_merchant_shop_active(
        table,
        shop_host,
        payload,
        headers,
        req_id,
        http_path="/api/me/merchant-premium-rules",
        audit_action="MERCHANT_PREMIUM_RULES_PUT",
    )
    if err:
        return err
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    if not isinstance(body, dict):
        return _resp(400, {"error": "body_must_be_object"})
    verr = validate_rules(table, body)
    if verr:
        return _resp(400, {"error": "invalid_merchant_premium_rules", "detail": verr})
    now = datetime.now(timezone.utc).isoformat()
    stored = normalize_for_storage(body)
    table.update_item(
        Key={"pk": pk_shop(shop_host), "sk": SK_METADATA},
        UpdateExpression="SET #mr = :mr, #u = :u",
        ExpressionAttributeNames={"#mr": MERCHANT_PREMIUM_RULES_JSON, "#u": "updated_at"},
        ExpressionAttributeValues={":mr": stored, ":u": now},
    )
    append_audit(
        table,
        shop_host,
        actor_type="merchant",
        actor_id=str(payload.get("sub") or ""),
        action="MERCHANT_PREMIUM_RULES_PUT",
        outcome="ok",
        detail={"version": body.get("version")},
        http_path="/api/me/merchant-premium-rules",
        request_id=req_id,
        source_ip=_xff(headers),
    )
    return _resp(200, {"ok": True})


def _get_merchant_app_config(table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item, err = _require_merchant_shop_active(
        table,
        shop_host,
        payload,
        headers,
        req_id,
        http_path="/api/me/app-config",
        audit_action="MERCHANT_APP_CONFIG_GET",
    )
    if err:
        return err
    return _resp(200, {"appConfig": parse_app_config_from_meta(item)})


def _patch_merchant_app_config(event, table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item, err = _require_merchant_shop_active(
        table,
        shop_host,
        payload,
        headers,
        req_id,
        http_path="/api/me/app-config",
        audit_action="MERCHANT_APP_CONFIG_PATCH",
    )
    if err:
        return err
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    patch, verr = validate_app_config_patch(body)
    if verr:
        err_body: dict = {"error": "invalid_app_config"}
        if verr == "invalid_keys":
            err_body["allowed"] = sorted(APP_CONFIG_ALLOWED_KEYS.keys())
        else:
            err_body["detail"] = verr
        return _resp(400, err_body)
    existing = parse_app_config_from_meta(item)
    merged = merge_app_config(existing, patch)
    now = datetime.now(timezone.utc).isoformat()
    if should_remove_storage(merged):
        table.update_item(
            Key={"pk": pk_shop(shop_host), "sk": SK_METADATA},
            UpdateExpression="REMOVE #ac SET #u = :u",
            ExpressionAttributeNames={"#ac": MERCHANT_APP_CONFIG_JSON, "#u": "updated_at"},
            ExpressionAttributeValues={":u": now},
        )
    else:
        table.update_item(
            Key={"pk": pk_shop(shop_host), "sk": SK_METADATA},
            UpdateExpression="SET #ac = :ac, #u = :u",
            ExpressionAttributeNames={"#ac": MERCHANT_APP_CONFIG_JSON, "#u": "updated_at"},
            ExpressionAttributeValues={
                ":ac": normalize_app_config_for_storage(merged),
                ":u": now,
            },
        )
    append_audit(
        table,
        shop_host,
        actor_type="merchant",
        actor_id=str(payload.get("sub") or ""),
        action="MERCHANT_APP_CONFIG_PATCH",
        outcome="ok",
        detail={"patch": {k: ("removed" if v is None else "set") for k, v in patch.items()}},
        http_path="/api/me/app-config",
        request_id=req_id,
        source_ip=_xff(headers),
    )
    return _resp(200, {"ok": True, "appConfig": merged})


def _sync_shop_currencies(table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    if not item:
        return _resp(404, {"error": "shop_not_installed"})
    if item.get("installation_status") != "ACTIVE":
        return _resp(400, {"error": "shop_not_active"})
    if item.get("plugin_suspended"):
        return _resp(403, {"error": "plugin_suspended"})
    enc = item.get("access_token_enc")
    if not enc:
        return _resp(400, {"error": "missing_access_token"})
    kms_key_id = os.environ["KMS_KEY_ID"]
    try:
        shop_token = get_fresh_shop_access_token(
            table,
            shop_host,
            kms_key_id_fallback=kms_key_id,
            client_id=os.environ["SHOPIFY_CLIENT_ID"],
            client_secret=os.environ["SHOPIFY_CLIENT_SECRET"],
            meta=item,
        )
    except ShopifyAuth401Error as e:
        row = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item") or item
        return _resp(401, shopify_auth_401_response_body(e, row))
    except Exception as e:
        logger.exception("sync_currencies_token_resolve_failed", extra={"shop": shop_host})
        return _resp(500, {"error": "token_resolve_failed", "detail": str(e)[:400]})
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    fb = str(item.get("shop_currency_code") or "").strip().upper()
    try:
        codes = sync_shop_enabled_currencies(
            table, shop_host, shop_token, api_version, fallback_primary=fb or None
        )
    except Exception as e:
        return _resp(502, {"error": "sync_shop_currencies_failed", "detail": str(e)[:500]})
    now = datetime.now(timezone.utc).isoformat()
    append_audit(
        table,
        shop_host,
        actor_type="merchant",
        actor_id=str(payload.get("sub") or ""),
        action="SHOP_ENABLED_CURRENCIES_SYNC",
        outcome="ok",
        http_path="/api/shop-enabled-currencies/sync",
        request_id=req_id,
        source_ip=_xff(headers),
        detail={"count": len(codes), "synced_at": now},
    )
    return _resp(200, {"ok": True, "currencies": codes, "synced_at": now})


def _cart_config(event, table, req_id: str):
    headers = _headers(event)
    shop = (headers.get("x-gwofy-shop") or "").strip().lower().rstrip("/")
    sig = headers.get("x-gwofy-signature") or ""
    secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    if not shop or not verify_shop_body_hmac(secret, shop, raw, sig):
        return _resp(401, {"error": "invalid_hmac"})

    try:
        body = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    domain = str(body.get("shopDomain") or "").strip()
    shop_host = domain.lower().replace("https://", "").replace("http://", "").split("/")[0].rstrip("/")
    if not shop_host or shop_host != shop:
        return _resp(400, {"error": "shop_domain_mismatch", "hint": "X-Gwofy-Shop must match shopDomain host"})

    item = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not item or item.get("installation_status") != "ACTIVE":
        return _resp(404, {"error": "shop_not_installed"})
    if item.get("plugin_suspended"):
        return _resp(403, {"error": "plugin_suspended"})
    if str(item.get("activation_status") or "") != "ACTIVATED":
        return _resp(403, {"error": "shop_not_activated", "hint": "Complete activation (protection product) before cart config."})

    country_cc = str(body.get("country") or "").strip().upper()
    if not country_cc:
        return _resp(400, {"error": "country_required"})
    if not is_country_supported(table, country_cc):
        return _resp(400, {"error": "country_not_supported", "country": country_cc})

    country = str(body.get("country") or "")
    dbg = str(body.get("debugParam") or "").lower() in ("1", "true", "yes")

    payload = build_cart_plugin_response(
        table,
        item,
        country=country,
        debug_mode=dbg,
    )
    append_audit(
        table,
        shop,
        actor_type="merchant",
        actor_id="storefront_hmac",
        action="CART_CONFIG",
        outcome="ok",
        http_path="/api/cart-config",
        request_id=req_id,
        source_ip=_xff(headers),
    )
    return _resp(200, payload)


def _etag_matches(if_none_match: str, etag: str) -> bool:
    if not if_none_match or not etag:
        return False
    for part in if_none_match.split(","):
        token = part.strip().strip('"')
        if token == etag:
            return True
    return False


def _serve_app_storefront_js(method: str, headers: dict[str, str]):
    body_bytes, etag, version = get_app_storefront_asset()
    inm = headers.get("if-none-match") or ""
    if _etag_matches(inm, etag):
        return _js_resp(304, etag=etag, version=version)
    if method == "HEAD":
        return _js_resp(200, etag=etag, version=version)
    return _js_resp(200, body=body_bytes.decode("utf-8"), etag=etag, version=version)


def _serve_app_config_js(event, method: str, headers: dict[str, str], table):
    shop_host = shop_query_from_event(event)
    if not shop_host:
        return _resp(400, {"error": "missing_shop"})
    if not is_valid_shop_host(shop_host):
        return _resp(400, {"error": "invalid_shop_host"})

    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    if not item:
        return _resp(404, {"error": "shop_not_found"})
    if str(item.get("activation_status") or "") != "ACTIVATED":
        return _resp(403, {"error": "shop_not_activated"})

    merged = build_effective_gwofy_config(
        table, item, shop_host, storefront_js_version=APP_STOREFRONT_VERSION
    )
    updated_at = str(item.get("updated_at") or "")
    body, etag = get_app_config_js_for_shop(merged, shop_host, updated_at)
    if method == "HEAD":
        return _js_resp(200, etag=etag, version=APP_CONFIG_VERSION, cache_control=None)
    return _js_resp(200, body=body, etag=etag, version=APP_CONFIG_VERSION, cache_control=None)


def _js_resp(
    code: int,
    *,
    body: str = "",
    etag: str = "",
    version: str = "",
    cache_control: str | None = "public, max-age=3600, must-revalidate",
):
    hdrs = {
        "Content-Type": "application/javascript; charset=utf-8",
    }
    if cache_control is not None:
        hdrs["Cache-Control"] = cache_control
    if etag:
        hdrs["ETag"] = f'"{etag}"'
    if version:
        hdrs["X-Gwofy-Asset-Version"] = version
    return {"statusCode": code, "headers": hdrs, "body": body}


def _resp(code: int, body: dict):
    # DynamoDB returns numbers as Decimal; default=str matches admin_handler and avoids 500 on json.dumps.
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }

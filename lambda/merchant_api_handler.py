"""Merchant Session API: /api/me, activate, embed ack, storefront cart-config (HMAC)."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone

import boto3

from lib.activate_app import ActivateAppError, run_activate_app_safe
from lib.audit import append_audit
from lib.cart_config_response import build_cart_plugin_response
from lib.shipping_country_defaults import is_country_supported
from lib.logging_json import setup_logging
from lib.merchant_premium_rules import normalize_for_storage, parse_rules_from_meta, validate_rules
from lib.models import MERCHANT_PREMIUM_RULES_JSON, SK_METADATA, pk_shop
from lib.session_jwt import shop_host_from_payload, verify_session_token
from lib.shop_enabled_currencies import parse_shop_enabled_currencies_json, sync_shop_enabled_currencies
from lib.storefront_auth import verify_shop_body_hmac
from lib.shop_offline_access import get_fresh_shop_access_token
from lib.shopify_api import DEFAULT_API_VERSION

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

    if method == "GET" and path == "/api/me":
        return _api_me(table, shop_host, payload, headers, req_id)
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

    return _resp(404, {"error": "not_found"})


def _api_me(table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    cid = os.environ["SHOPIFY_CLIENT_ID"]
    embed_url = f"https://{shop_host}/admin/themes/current/editor?context=apps&activateAppId={cid}"
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
        "shop_enabled_currencies": sorted(parse_shop_enabled_currencies_json(item)),
        "shop_enabled_currencies_synced_at": item.get("shop_enabled_currencies_synced_at"),
    }

    _maybe_enqueue_profile_refresh(table, shop_host, item)

    return _resp(200, {"session": payload.get("sub"), "shop_metadata": safe})


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
    try:
        shop_token = get_fresh_shop_access_token(
            table,
            shop_host,
            kms_key_id_fallback=kms_key_id,
            client_id=os.environ["SHOPIFY_CLIENT_ID"],
            client_secret=os.environ["SHOPIFY_CLIENT_SECRET"],
            meta=item,
        )
    except Exception as e:
        logger.exception("activate_token_resolve_failed", extra={"shop": shop_host})
        return _resp(500, {"error": "token_resolve_failed", "detail": str(e)[:400]})

    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
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
        )
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


def _resp(code: int, body: dict):
    # DynamoDB returns numbers as Decimal; default=str matches admin_handler and avoids 500 on json.dumps.
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }

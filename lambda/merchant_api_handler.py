"""Merchant Session API: /api/me, activate, embed ack, protection variant resolve."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

from lib.audit import append_audit
from lib.cart_config_response import build_cart_plugin_response
from lib.shipping_country_defaults import (
    effective_max_coverage_usd,
    is_country_supported,
)
from lib.kms_tokens import decrypt_token
from lib.logging_json import setup_logging
from lib.models import SK_METADATA, pk_shop
from lib.pricing_config import get_pricing_model
from lib.pricing_resolve import pick_tier, shop_to_usd
from lib.session_jwt import shop_host_from_payload, verify_session_token
from lib.storefront_auth import verify_shop_body_hmac
from lib.variant_lookup import variant_gid_for_plan

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

    if method == "POST" and path == "/api/protection/resolve-variant":
        return _resolve_variant(event, table, req_id)

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

    return _resp(404, {"error": "not_found"})


def _api_me(table, shop_host: str, payload: dict, headers: dict, req_id: str):
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    if not item:
        return _resp(404, {"error": "shop_not_installed"})
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

    cid = os.environ["SHOPIFY_CLIENT_ID"]
    embed_url = f"https://{shop_host}/admin/themes/current/editor?context=apps&activateAppId={cid}"

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
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
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

    q = os.environ["WORK_QUEUE_URL"]
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
    sqs.send_message(
        QueueUrl=q,
        MessageBody=json.dumps(
            {
                "source": "merchant_api",
                "event": "ACTIVATE_APP",
                "shop": shop_host,
                "store_number": str(item.get("store_number", "")),
                "api_version": api_version,
                "actor_sub": str(payload.get("sub") or ""),
            }
        ),
    )
    return _resp(202, {"ok": True, "queued": True})


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

    total = body.get("cart_subtotal")
    if total is not None:
        cur = str(body.get("currency") or "").strip().upper()
        shop_ccy = str(item.get("shop_currency_code") or "").upper()
        if not cur or cur != shop_ccy:
            return _resp(400, {"error": "currency_mismatch", "expected": shop_ccy})
        rate_s = item.get("last_fx_usd_to_shop")
        if not rate_s:
            return _resp(400, {"error": "fx_missing_activate_first"})
        rate = float(rate_s)
        cart_shop = Decimal(str(total))
        cart_usd = shop_to_usd(cart_shop, rate)
        max_usd = float(effective_max_coverage_usd(table, item, country_cc))
        if cart_usd > Decimal(str(max_usd)):
            return _resp(
                400,
                {
                    "error": "cart_exceeds_max_coverage",
                    "max_coverage_usd": max_usd,
                    "cart_subtotal_usd": str(cart_usd),
                },
            )

    country = str(body.get("country") or "")
    currency = str(body.get("currency") or "")
    language = str(body.get("language") or "en")
    dbg = str(body.get("debugParam") or "").lower() in ("1", "true", "yes")

    payload = build_cart_plugin_response(
        table,
        item,
        country=country,
        currency=currency,
        language=language,
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


def _resolve_variant(event, table, req_id: str):
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

    total = body.get("cart_subtotal")
    currency = (body.get("currency") or "").strip().upper()
    if total is None or not currency:
        return _resp(400, {"error": "cart_subtotal_and_currency_required"})

    item = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not item or item.get("installation_status") != "ACTIVE":
        return _resp(404, {"error": "shop_not_installed"})
    if item.get("plugin_suspended"):
        return _resp(403, {"error": "plugin_suspended"})
    if str(item.get("activation_status") or "") != "ACTIVATED":
        return _resp(403, {"error": "shop_not_activated", "hint": "Complete activation before resolving a variant."})
    pid = item.get("protection_product_gid")
    if not pid:
        return _resp(400, {"error": "protection_product_missing_activate_first"})

    shop_ccy = str(item.get("shop_currency_code") or "").upper()
    if currency != shop_ccy:
        return _resp(400, {"error": "currency_mismatch", "expected": shop_ccy})

    rate_s = item.get("last_fx_usd_to_shop")
    if not rate_s:
        return _resp(400, {"error": "fx_missing_activate_first"})
    rate = float(rate_s)
    cart_shop = Decimal(str(total))
    cart_usd = shop_to_usd(cart_shop, rate)

    country_opt = str(body.get("country") or "").strip().upper()
    if country_opt:
        if not is_country_supported(table, country_opt):
            return _resp(400, {"error": "country_not_supported", "country": country_opt})
        max_usd = float(effective_max_coverage_usd(table, item, country_opt))
        if cart_usd > Decimal(str(max_usd)):
            return _resp(
                400,
                {
                    "error": "cart_exceeds_max_coverage",
                    "max_coverage_usd": max_usd,
                    "cart_subtotal_usd": str(cart_usd),
                },
            )
    else:
        max_legacy = float(item.get("sp_max_coverage_usd") or 9000)
        if cart_usd > Decimal(str(max_legacy)):
            return _resp(
                400,
                {
                    "error": "cart_exceeds_max_coverage",
                    "max_coverage_usd": max_legacy,
                    "cart_subtotal_usd": str(cart_usd),
                },
            )

    tier = pick_tier(get_pricing_model(table), cart_usd)
    if not tier:
        return _resp(404, {"error": "no_tier"})

    plan_code = str(tier.get("plan_code") or "")
    enc = item.get("access_token_enc")
    if not enc:
        return _resp(500, {"error": "no_token"})
    kms_key = item.get("kms_key_id") or os.environ["KMS_KEY_ID"]
    access = decrypt_token(kms_key, enc)
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
    vid = variant_gid_for_plan(shop, access, api_version, str(pid), plan_code)
    if not vid:
        return _resp(404, {"error": "variant_not_found", "plan_code": plan_code})

    price_shop = (Decimal(str(tier.get("price_usd", 0))) * Decimal(str(rate))).quantize(Decimal("0.01"))

    append_audit(
        table,
        shop,
        actor_type="merchant",
        actor_id="storefront_hmac",
        action="PROTECTION_RESOLVE",
        outcome="ok",
        detail={"plan_code": plan_code, "variant_gid_prefix": vid[:40]},
        http_path="/api/protection/resolve-variant",
        request_id=req_id,
        source_ip=_xff(headers),
    )
    return _resp(
        200,
        {
            "variant_id": vid,
            "plan_code": plan_code,
            "price": str(price_shop),
            "currency": currency,
        },
    )


def _resp(code: int, body: dict):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }

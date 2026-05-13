"""Admin HTTP API — Cognito JWT verified at API Gateway."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import boto3
from boto3.dynamodb.conditions import Attr

from lib.admin_cognito import admin_in_required_group, cognito_groups_from_claims
from lib.audit import append_audit
from lib.logging_json import setup_logging
from lib.calc_coverage_tips_config import (
    effective_calc_coverage_tips,
    get_calc_coverage_tips_global,
    put_calc_coverage_tips_global,
    shop_override_snapshot,
    validate_shop_tip_value,
)
from lib.models import (
    GSI2_PK_SHOP_INDEX,
    META_SP_BELOW_MIN_COVERAGE_TIP,
    META_SP_GREATER_MAX_COVERAGE_TIP,
    SK_AUDIT_PREFIX,
    SK_METADATA,
    pk_shop,
    pk_tenant,
)
from lib.activity_config import get_activity_info, put_activity_info
from lib.tips_config import get_tips_info, put_tips_info
from lib.pricing_config import (
    get_pricing_model,
    get_supported_currencies,
    put_pricing_model,
    put_supported_currencies,
)
from lib.max_coverage_config import (
    get_global_max_coverage_by_currency,
    normalize_shop_max_coverage_for_storage,
    put_global_max_coverage_by_currency,
    validate_shop_max_coverage_by_currency,
)
from lib.merchant_premium_rules import parse_rules_from_meta
from lib.kms_tokens import decrypt_token
from lib.shop_enabled_currencies import parse_shop_enabled_currencies_json, sync_shop_enabled_currencies
from lib.shipping_country_defaults import get_shipping_country_defaults, put_shipping_country_defaults

logger = setup_logging("admin")

ddb = boto3.resource("dynamodb")


def _claims(event) -> dict[str, Any]:
    rc = event.get("requestContext") or {}
    auth = rc.get("authorizer") or {}
    return (auth.get("jwt") or {}).get("claims") or {}


def _http(event):
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    return http.get("method", "GET").upper(), http.get("path", ""), rc.get("requestId", "")


def handler(event, context):
    method, path, req_id = _http(event)
    claims = _claims(event)
    ok_group, required_group = admin_in_required_group(claims)
    if not ok_group:
        pg = cognito_groups_from_claims(claims)
        logger.info(
            "admin_forbidden_missing_group",
            extra={
                "sub": claims.get("sub"),
                "required_group": required_group,
                "parsed_groups": pg,
            },
        )
        return _resp(
            403,
            {
                "error": "forbidden_not_in_admin_group",
                "required_group": required_group,
                "parsed_groups": pg,
                "hint": "Use Cognito Id token; user must be in this group (provisioned on your platform).",
            },
        )

    actor_sub = str(claims.get("sub") or "unknown")
    actor_email = str(claims.get("email") or "")

    table = ddb.Table(os.environ["TABLE_NAME"])

    cfg_parts = path.strip("/").split("/")
    if len(cfg_parts) >= 3 and cfg_parts[0] == "admin" and cfg_parts[1] == "config":
        if cfg_parts[2] == "supported-currencies":
            if method == "GET":
                return _resp(200, {"currencies": get_supported_currencies(table)})
            if method == "PUT":
                return _put_supported_currencies(event, table, actor_sub, actor_email, req_id)
        if len(cfg_parts) == 4 and cfg_parts[2] == "pricing-model":
            ccy = unquote(cfg_parts[3]).strip().upper()
            if method == "GET":
                return _resp(200, {"currency": ccy, "tiers": get_pricing_model(table, ccy)})
            if method == "PUT":
                return _put_pricing_currency(event, table, ccy, actor_sub, actor_email, req_id)

    if method == "GET" and path == "/admin/config/pricing-model":
        return _resp(200, {"tiers": get_pricing_model(table, "USD")})

    if method == "PUT" and path == "/admin/config/pricing-model":
        return _resp(
            400,
            {
                "error": "deprecated_use_pricing_model_currency",
                "hint": "PUT /admin/config/pricing-model/USD (or other ISO currency code)",
            },
        )

    if method == "GET" and path == "/admin/config/shipping-countries":
        return _resp(200, {"countries": get_shipping_country_defaults(table)})
    if method == "PUT" and path == "/admin/config/shipping-countries":
        return _put_shipping_countries(event, table, actor_sub, actor_email, req_id)

    if method == "GET" and path == "/admin/config/max-coverage-by-currency":
        return _resp(200, {"amounts": get_global_max_coverage_by_currency(table)})
    if method == "PUT" and path == "/admin/config/max-coverage-by-currency":
        return _put_max_coverage_by_currency(event, table, actor_sub, actor_email, req_id)

    if method == "GET" and path == "/admin/config/activity-info":
        return _resp(200, get_activity_info(table))

    if method == "PUT" and path == "/admin/config/activity-info":
        return _put_activity_info(event, table, actor_sub, actor_email, req_id)

    if method == "GET" and path == "/admin/config/tips-info":
        return _resp(200, get_tips_info(table))

    if method == "PUT" and path == "/admin/config/tips-info":
        return _put_tips_info(event, table, actor_sub, actor_email, req_id)

    if method == "GET" and path == "/admin/config/calc-coverage-tips":
        return _resp(200, get_calc_coverage_tips_global(table))

    if method == "PUT" and path == "/admin/config/calc-coverage-tips":
        return _put_calc_coverage_tips_global(event, table, actor_sub, actor_email, req_id)

    if method == "GET" and path == "/admin/shops":
        return _list_shops(event, table, actor_sub, req_id)

    parts = path.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "admin" or parts[1] != "shops":
        return _resp(404, {"error": "not_found"})

    shop_enc = parts[2]
    shop = unquote(shop_enc).strip().lower().rstrip("/")

    if method == "GET" and len(parts) == 3:
        return _get_shop(table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "detail":
        return _get_shop_detail(table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "products":
        return _list_products(table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "orders":
        return _list_orders(event, table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "audit":
        return _list_audit(event, table, shop, actor_sub, req_id)

    if method == "PUT" and len(parts) == 4 and parts[3] == "shipping-calc-settings":
        return _put_shipping_calc(event, table, shop, actor_sub, actor_email, req_id)

    if method == "POST" and len(parts) == 4 and parts[3] == "sync-enabled-currencies":
        return _post_admin_sync_shop_currencies(event, table, shop, actor_sub, actor_email, req_id)

    if method == "GET" and len(parts) == 4 and parts[3] == "calc-coverage-tips":
        return _get_shop_calc_coverage_tips(table, shop, actor_sub, req_id)

    if method == "PUT" and len(parts) == 4 and parts[3] == "calc-coverage-tips":
        return _put_shop_calc_coverage_tips(event, table, shop, actor_sub, actor_email, req_id)

    if method == "POST" and len(parts) == 5 and parts[3] == "features" and parts[4] == "return-insurance":
        return _feature_return(event, table, shop, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 5 and parts[3] == "features" and parts[4] == "shipping-protection":
        return _feature_shipping(event, table, shop, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 4 and parts[3] == "suspend":
        return _suspend(table, shop, True, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 4 and parts[3] == "resume":
        return _suspend(table, shop, False, actor_sub, actor_email, req_id)

    return _resp(404, {"error": "not_found"})


def _put_supported_currencies(event, table, actor_sub: str, actor_email: str, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    cur = body.get("currencies")
    if not isinstance(cur, list):
        return _resp(400, {"error": "currencies_array_required"})
    try:
        put_supported_currencies(table, [str(x) for x in cur], actor_sub)
    except ValueError as e:
        return _resp(400, {"error": "invalid_supported_currencies", "detail": str(e)})
    append_audit(
        table,
        "_gwofy_system_",
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_SUPPORTED_CURRENCIES_UPDATE",
        outcome="ok",
        resource="supported_currencies",
        actor_email=actor_email or None,
        detail={"currencies": cur},
        http_path="/admin/config/supported-currencies",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _put_pricing_currency(
    event, table, currency: str, actor_sub: str, actor_email: str, req_id: str
):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    tiers = body.get("tiers")
    if not isinstance(tiers, list):
        return _resp(400, {"error": "tiers_array_required"})
    try:
        put_pricing_model(table, tiers, actor_sub, currency=currency)
    except ValueError as e:
        return _resp(400, {"error": "invalid_tiers", "detail": str(e)})
    http_path = event.get("requestContext", {}).get("http", {}).get("path", "")
    append_audit(
        table,
        "_gwofy_system_",
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_PRICING_UPDATE",
        outcome="ok",
        resource="pricing_model",
        actor_email=actor_email or None,
        detail={"tier_count": len(tiers), "currency": currency},
        http_path=http_path,
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _put_calc_coverage_tips_global(event, table, actor_sub: str, actor_email: str, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    try:
        put_calc_coverage_tips_global(table, body, actor_sub)
    except ValueError as e:
        return _resp(400, {"error": "invalid_calc_coverage_tips", "detail": str(e)})
    append_audit(
        table,
        "_gwofy_system_",
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_CALC_COVERAGE_TIPS_GLOBAL_UPDATE",
        outcome="ok",
        resource="calc_coverage_tips_global",
        actor_email=actor_email or None,
        http_path="/admin/config/calc-coverage-tips",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _get_shop_calc_coverage_tips(table, shop: str, actor_sub: str, req_id: str):
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    return _resp(
        200,
        {
            "global": get_calc_coverage_tips_global(table),
            "shopOverride": shop_override_snapshot(meta),
            "effective": effective_calc_coverage_tips(table, meta),
        },
    )


def _put_shop_calc_coverage_tips(event, table, shop: str, actor_sub: str, actor_email: str, req_id: str):
    pk = pk_shop(shop)
    meta = table.get_item(Key={"pk": pk, "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    if not isinstance(body, dict):
        return _resp(400, {"error": "invalid_body"})
    allowed_keys = {"spBelowMinCoverageTip", "spGreaterMaxCoverageTip"}
    if not body or set(body.keys()) - allowed_keys:
        return _resp(400, {"error": "invalid_keys", "allowed": sorted(allowed_keys)})
    mapping = {
        "spBelowMinCoverageTip": META_SP_BELOW_MIN_COVERAGE_TIP,
        "spGreaterMaxCoverageTip": META_SP_GREATER_MAX_COVERAGE_TIP,
    }
    sets: dict[str, str] = {}
    removes: list[str] = []
    for api_k, meta_k in mapping.items():
        if api_k not in body:
            continue
        try:
            v = validate_shop_tip_value(body[api_k])
        except ValueError as e:
            return _resp(400, {"error": "invalid_tip", "detail": str(e)})
        if v is None:
            removes.append(meta_k)
        else:
            sets[meta_k] = v
    if not sets and not removes:
        return _resp(400, {"error": "no_fields_to_update"})
    now = datetime.now(timezone.utc).isoformat()
    names: dict[str, str] = {}
    values: dict[str, Any] = {":u": now}
    set_parts: list[str] = []
    idx = 0
    for k, v in sets.items():
        nk = f"#m{idx}"
        vk = f":t{idx}"
        names[nk] = k
        values[vk] = v
        set_parts.append(f"{nk} = {vk}")
        idx += 1
    remove_parts: list[str] = []
    for k in removes:
        nk = f"#m{idx}"
        names[nk] = k
        remove_parts.append(nk)
        idx += 1
    if set_parts:
        ue = "SET " + ", ".join(set_parts) + ", updated_at = :u"
    else:
        ue = "SET updated_at = :u"
    if remove_parts:
        ue += " REMOVE " + ", ".join(remove_parts)
    kwargs: dict[str, Any] = {
        "Key": {"pk": pk, "sk": SK_METADATA},
        "UpdateExpression": ue,
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }
    table.update_item(**kwargs)
    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_SHOP_CALC_COVERAGE_TIPS_UPDATE",
        outcome="ok",
        resource="calc_coverage_tips_shop",
        actor_email=actor_email or None,
        detail={"sets": list(sets.keys()), "removes": removes},
        http_path=f"/admin/shops/{shop}/calc-coverage-tips",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _put_tips_info(event, table, actor_sub: str, actor_email: str, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    try:
        put_tips_info(table, body, actor_sub)
    except ValueError as e:
        return _resp(400, {"error": "invalid_tips_info", "detail": str(e)})
    append_audit(
        table,
        "_gwofy_system_",
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_TIPS_INFO_UPDATE",
        outcome="ok",
        resource="tips_info",
        actor_email=actor_email or None,
        http_path="/admin/config/tips-info",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _put_activity_info(event, table, actor_sub: str, actor_email: str, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    try:
        put_activity_info(table, body, actor_sub)
    except ValueError as e:
        return _resp(400, {"error": "invalid_activity_info", "detail": str(e)})
    append_audit(
        table,
        "_gwofy_system_",
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_ACTIVITY_INFO_UPDATE",
        outcome="ok",
        resource="activity_info",
        actor_email=actor_email or None,
        http_path="/admin/config/activity-info",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _put_shipping_countries(event, table, actor_sub: str, actor_email: str, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    countries = body.get("countries")
    if not isinstance(countries, dict):
        return _resp(400, {"error": "countries_object_required"})
    try:
        put_shipping_country_defaults(table, countries, actor_sub)
    except ValueError as e:
        return _resp(400, {"error": "invalid_countries", "detail": str(e)})
    append_audit(
        table,
        "_gwofy_system_",
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_SHIPPING_COUNTRIES_UPDATE",
        outcome="ok",
        resource="shipping_country_defaults",
        actor_email=actor_email or None,
        detail={"country_count": len(countries)},
        http_path="/admin/config/shipping-countries",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _put_max_coverage_by_currency(event, table, actor_sub: str, actor_email: str, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    amounts = body.get("amounts")
    if not isinstance(amounts, dict):
        return _resp(400, {"error": "amounts_object_required"})
    try:
        put_global_max_coverage_by_currency(table, amounts, actor_sub)
    except ValueError as e:
        return _resp(400, {"error": "invalid_max_coverage_amounts", "detail": str(e)})
    append_audit(
        table,
        "_gwofy_system_",
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_MAX_COVERAGE_BY_CURRENCY_UPDATE",
        outcome="ok",
        resource="max_coverage_by_currency",
        actor_email=actor_email or None,
        detail={"currency_count": len(amounts)},
        http_path="/admin/config/max-coverage-by-currency",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _post_admin_sync_shop_currencies(event, table, shop: str, actor_sub: str, actor_email: str, req_id: str):
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    enc = meta.get("access_token_enc")
    if not enc:
        return _resp(400, {"error": "missing_access_token"})
    kms_key_id = os.environ["KMS_KEY_ID"]
    key_id = str(meta.get("kms_key_id") or kms_key_id)
    try:
        token = decrypt_token(key_id, str(enc))
    except Exception as e:
        return _resp(500, {"error": "token_decrypt_failed", "detail": str(e)[:200]})
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
    fb = str(meta.get("shop_currency_code") or "").strip().upper()
    try:
        codes = sync_shop_enabled_currencies(
            table, shop, token, api_version, fallback_primary=fb or None
        )
    except Exception as e:
        return _resp(502, {"error": "sync_shop_currencies_failed", "detail": str(e)[:500]})
    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_SHOP_ENABLED_CURRENCIES_SYNC",
        outcome="ok",
        resource="shop_enabled_currencies",
        actor_email=actor_email or None,
        detail={"count": len(codes)},
        http_path="/admin/shops/.../sync-enabled-currencies",
        request_id=req_id,
    )
    return _resp(200, {"ok": True, "currencies": codes})


def _put_shipping_calc(event, table, shop: str, actor_sub: str, actor_email: str, req_id: str):
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    if body.get("sp_country_max_overrides") is not None:
        return _resp(
            400,
            {
                "error": "deprecated_sp_country_max_overrides",
                "hint": "Use sp_max_coverage_by_currency (currency-keyed amounts) instead.",
            },
        )
    max_cov = body.get("sp_max_coverage_usd")
    rates = body.get("sp_market_rates")
    max_by_ccy = body.get("sp_max_coverage_by_currency")
    if max_cov is None and rates is None and max_by_ccy is None:
        return _resp(
            400,
            {
                "error": "one_of_sp_max_coverage_usd_sp_market_rates_sp_max_coverage_by_currency_required",
            },
        )

    names: dict[str, str] = {"#u": "updated_at"}
    vals: dict[str, Any] = {":u": datetime.now(timezone.utc).isoformat()}
    parts_expr = ["#u = :u"]

    if max_cov is not None:
        try:
            dmax = Decimal(str(max_cov))
        except Exception:
            return _resp(400, {"error": "invalid_sp_max_coverage_usd"})
        if dmax <= 0:
            return _resp(400, {"error": "sp_max_coverage_usd_must_be_positive"})
        names["#mx"] = "sp_max_coverage_usd"
        vals[":mx"] = dmax
        parts_expr.append("#mx = :mx")

    if rates is not None:
        if not isinstance(rates, dict):
            return _resp(400, {"error": "sp_market_rates_must_be_object"})
        norm = {str(k).upper(): str(v) for k, v in rates.items()}
        names["#mr"] = "sp_market_rates_json"
        vals[":mr"] = json.dumps(norm, ensure_ascii=False, sort_keys=True)
        parts_expr.append("#mr = :mr")

    if max_by_ccy is not None:
        if not isinstance(max_by_ccy, dict):
            return _resp(400, {"error": "sp_max_coverage_by_currency_must_be_object"})
        allowed = parse_shop_enabled_currencies_json(meta)
        if not allowed:
            return _resp(
                400,
                {
                    "error": "shop_enabled_currencies_not_synced",
                    "hint": "POST /api/shop-enabled-currencies/sync or POST /admin/shops/{shop}/sync-enabled-currencies",
                },
            )
        verr = validate_shop_max_coverage_by_currency(max_by_ccy, allowed)
        if verr:
            return _resp(400, {"error": "invalid_sp_max_coverage_by_currency", "detail": verr})
        try:
            norm_m = normalize_shop_max_coverage_for_storage(max_by_ccy)
        except ValueError as e:
            return _resp(400, {"error": "invalid_sp_max_coverage_by_currency", "detail": str(e)})
        names["#mb"] = "sp_max_coverage_by_currency_json"
        vals[":mb"] = json.dumps(norm_m, ensure_ascii=False, sort_keys=True)
        parts_expr.append("#mb = :mb")

    table.update_item(
        Key={"pk": pk_shop(shop), "sk": SK_METADATA},
        UpdateExpression="SET " + ", ".join(parts_expr),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )
    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_SHIPPING_CALC_SETTINGS",
        outcome="ok",
        resource="shipping_calc",
        actor_email=actor_email or None,
        detail={
            "has_max": max_cov is not None,
            "has_rates": rates is not None,
            "has_max_by_currency": max_by_ccy is not None,
        },
        http_path="/admin/shops/.../shipping-calc-settings",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _list_shops(event, table, actor_sub: str, req_id: str):
    qs = event.get("queryStringParameters") or {}
    status = (qs.get("status") or "").strip().upper() or None
    limit = min(int(qs.get("limit") or 50), 100)
    cursor_b64 = qs.get("cursor") or ""
    eks = None
    if cursor_b64:
        try:
            eks = json.loads(base64.urlsafe_b64decode(cursor_b64.encode("ascii")).decode("utf-8"))
        except Exception:
            return _resp(400, {"error": "invalid_cursor"})

    kwargs: dict[str, Any] = {
        "IndexName": "GSI2",
        "KeyConditionExpression": "gsi2pk = :p",
        "ExpressionAttributeValues": {":p": GSI2_PK_SHOP_INDEX},
        "Limit": limit,
        "ScanIndexForward": False,
    }
    if eks:
        kwargs["ExclusiveStartKey"] = eks
    if status:
        kwargs["FilterExpression"] = Attr("installation_status").eq(status)

    resp = table.query(**kwargs)
    items = []
    for it in resp.get("Items", []):
        if it.get("sk") != SK_METADATA:
            continue
        items.append(
            {
                "shop": it.get("shop"),
                "store_number": it.get("store_number"),
                "auth_id": it.get("store_number"),
                "installation_status": it.get("installation_status"),
                "activation_status": it.get("activation_status"),
                "return_insurance_status": it.get("return_insurance_status"),
                "shipping_protection_status": it.get("shipping_protection_status"),
                "plugin_suspended": it.get("plugin_suspended"),
                "shop_currency_code": it.get("shop_currency_code"),
            }
        )
    next_cursor = None
    lek = resp.get("LastEvaluatedKey")
    if lek:
        next_cursor = base64.urlsafe_b64encode(json.dumps(lek, default=str).encode("utf-8")).decode("ascii")
    return _resp(200, {"items": items, "next_cursor": next_cursor})


def _get_shop(table, shop: str, actor_sub: str, req_id: str):
    it = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not it:
        return _resp(404, {"error": "not_found"})
    out = dict(it)
    out["shop_enabled_currencies"] = sorted(parse_shop_enabled_currencies_json(it))
    return _resp(200, {"shop": out})


def _get_shop_detail(table, shop: str, actor_sub: str, req_id: str):
    it = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not it:
        return _resp(404, {"error": "not_found"})
    out = dict(it)
    out["shop_enabled_currencies"] = sorted(parse_shop_enabled_currencies_json(it))
    rules, warn = parse_rules_from_meta(table, it)
    payload: dict[str, Any] = {"shop": out, "merchantPremiumRules": rules}
    if warn:
        payload["merchant_premium_rules_parse_warning"] = warn
    return _resp(200, payload)


def _list_products(table, shop: str, actor_sub: str, req_id: str):
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    sn = str(meta.get("store_number", ""))
    pk_t = pk_tenant(sn)
    resp = table.query(
        KeyConditionExpression="pk = :p AND begins_with(sk, :pre)",
        ExpressionAttributeValues={":p": pk_t, ":pre": "PRODUCT#"},
        Limit=100,
    )
    return _resp(200, {"items": resp.get("Items", [])})


def _list_orders(event, table, shop: str, actor_sub: str, req_id: str):
    qs = event.get("queryStringParameters") or {}
    only_prot = (qs.get("only_protection") or "").lower() in ("1", "true", "yes")
    tag_filter = (qs.get("tag") or "").strip()
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    sn = str(meta.get("store_number", ""))
    pk_t = pk_tenant(sn)
    resp = table.query(
        KeyConditionExpression="pk = :p AND begins_with(sk, :pre)",
        ExpressionAttributeValues={":p": pk_t, ":pre": "ORDER#"},
        Limit=100,
    )
    items = resp.get("Items", [])
    if only_prot:
        items = [x for x in items if x.get("has_shipping_protection")]
    if tag_filter:
        items = [x for x in items if tag_filter in (x.get("sync_tags") or [])]
    return _resp(200, {"items": items})


def _list_audit(event, table, shop: str, actor_sub: str, req_id: str):
    qs = event.get("queryStringParameters") or {}
    limit = min(int(qs.get("limit") or 50), 100)
    cursor_b64 = qs.get("cursor") or ""
    eks = None
    if cursor_b64:
        try:
            eks = json.loads(base64.urlsafe_b64decode(cursor_b64.encode("ascii")).decode("utf-8"))
        except Exception:
            return _resp(400, {"error": "invalid_cursor"})
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :pre)",
        "ExpressionAttributeValues": {":pk": pk_shop(shop), ":pre": SK_AUDIT_PREFIX},
        "Limit": limit,
        "ScanIndexForward": False,
    }
    if eks:
        kwargs["ExclusiveStartKey"] = eks
    resp = table.query(**kwargs)
    next_cursor = None
    lek = resp.get("LastEvaluatedKey")
    if lek:
        next_cursor = base64.urlsafe_b64encode(json.dumps(lek, default=str).encode("utf-8")).decode("ascii")
    return _resp(200, {"items": resp.get("Items", []), "next_cursor": next_cursor})


def _feature_return(event, table, shop: str, actor_sub: str, actor_email: str, req_id: str):
    return _feature_set(event, table, shop, "return_insurance_status", actor_sub, actor_email, req_id)


def _feature_shipping(event, table, shop: str, actor_sub: str, actor_email: str, req_id: str):
    return _feature_set(event, table, shop, "shipping_protection_status", actor_sub, actor_email, req_id)


def _feature_set(event, table, shop: str, field: str, actor_sub: str, actor_email: str, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    status = body.get("status")
    allowed = {"CLOSED", "OPEN_UNAUDITED", "OPEN_AUDITED"}
    if status not in allowed:
        return _resp(400, {"error": "invalid_status", "allowed": list(allowed)})
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop), "sk": SK_METADATA},
        UpdateExpression=f"SET {field} = :s, updated_at = :u",
        ExpressionAttributeValues={":s": status, ":u": now},
    )
    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_FEATURE_UPDATE",
        outcome="ok",
        resource=field,
        actor_email=actor_email or None,
        detail={"status": status},
        http_path=event.get("requestContext", {}).get("http", {}).get("path"),
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _suspend(table, shop: str, suspend: bool, actor_sub: str, actor_email: str, req_id: str):
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop), "sk": SK_METADATA},
        UpdateExpression="SET plugin_suspended = :p, updated_at = :u",
        ExpressionAttributeValues={":p": suspend, ":u": now},
    )
    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_SUSPEND" if suspend else "ADMIN_RESUME",
        outcome="ok",
        detail={"plugin_suspended": suspend},
        actor_email=actor_email or None,
        http_path="/admin/shops/.../suspend",
        request_id=req_id,
    )
    return _resp(200, {"ok": True})


def _resp(code: int, body: dict):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }

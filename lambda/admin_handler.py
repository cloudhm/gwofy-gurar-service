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
from lib.models import GSI2_PK_SHOP_INDEX, SK_AUDIT_PREFIX, SK_METADATA, pk_shop, pk_tenant
from lib.pricing_config import put_pricing_model
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

    if method == "PUT" and path == "/admin/config/pricing-model":
        return _put_pricing(event, table, actor_sub, actor_email, req_id)

    if method == "GET" and path == "/admin/config/shipping-countries":
        return _resp(200, {"countries": get_shipping_country_defaults(table)})
    if method == "PUT" and path == "/admin/config/shipping-countries":
        return _put_shipping_countries(event, table, actor_sub, actor_email, req_id)

    if method == "GET" and path == "/admin/shops":
        return _list_shops(event, table, actor_sub, req_id)

    parts = path.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "admin" or parts[1] != "shops":
        return _resp(404, {"error": "not_found"})

    shop_enc = parts[2]
    shop = unquote(shop_enc).strip().lower().rstrip("/")

    if method == "GET" and len(parts) == 3:
        return _get_shop(table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "products":
        return _list_products(table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "orders":
        return _list_orders(event, table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "audit":
        return _list_audit(event, table, shop, actor_sub, req_id)

    if method == "PUT" and len(parts) == 4 and parts[3] == "shipping-calc-settings":
        return _put_shipping_calc(event, table, shop, actor_sub, actor_email, req_id)

    if method == "POST" and len(parts) == 5 and parts[3] == "features" and parts[4] == "return-insurance":
        return _feature_return(event, table, shop, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 5 and parts[3] == "features" and parts[4] == "shipping-protection":
        return _feature_shipping(event, table, shop, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 4 and parts[3] == "suspend":
        return _suspend(table, shop, True, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 4 and parts[3] == "resume":
        return _suspend(table, shop, False, actor_sub, actor_email, req_id)

    return _resp(404, {"error": "not_found"})


def _put_pricing(event, table, actor_sub: str, actor_email: str, req_id: str):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    tiers = body.get("tiers")
    if not isinstance(tiers, list):
        return _resp(400, {"error": "tiers_array_required"})
    try:
        put_pricing_model(table, tiers, actor_sub)
    except ValueError as e:
        return _resp(400, {"error": "invalid_tiers", "detail": str(e)})
    append_audit(
        table,
        "_gwofy_system_",
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_PRICING_UPDATE",
        outcome="ok",
        resource="pricing_model",
        actor_email=actor_email or None,
        detail={"tier_count": len(tiers)},
        http_path="/admin/config/pricing-model",
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


def _put_shipping_calc(event, table, shop: str, actor_sub: str, actor_email: str, req_id: str):
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    max_cov = body.get("sp_max_coverage_usd")
    rates = body.get("sp_market_rates")
    max_over = body.get("sp_country_max_overrides")
    if max_cov is None and rates is None and max_over is None:
        return _resp(
            400,
            {"error": "one_of_sp_max_coverage_usd_sp_market_rates_sp_country_max_overrides_required"},
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

    if max_over is not None:
        if not isinstance(max_over, dict):
            return _resp(400, {"error": "sp_country_max_overrides_must_be_object"})
        norm_m: dict[str, float] = {}
        for k, v in max_over.items():
            cc = str(k).upper()
            try:
                norm_m[cc] = float(v)
            except (TypeError, ValueError):
                return _resp(400, {"error": "invalid_sp_country_max_overrides", "country": cc})
            if norm_m[cc] <= 0:
                return _resp(400, {"error": "max_override_must_be_positive", "country": cc})
        names["#mo"] = "sp_country_max_overrides_json"
        vals[":mo"] = json.dumps(norm_m, ensure_ascii=False, sort_keys=True)
        parts_expr.append("#mo = :mo")

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
            "has_max_overrides": max_over is not None,
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
    return _resp(200, {"shop": it})


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

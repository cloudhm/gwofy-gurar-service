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
    APP_CONFIG_SCRIPT_NAME,
    GSI2_PK_SHOP_INDEX,
    META_SP_BELOW_MIN_COVERAGE_TIP,
    META_SP_GREATER_MAX_COVERAGE_TIP,
    SK_AUDIT_PREFIX,
    SK_METADATA,
    STOREFRONT_CONFIG_JSON,
    pk_shop,
    pk_tenant,
)
from lib.static_assets import APP_STOREFRONT_VERSION, AppConfigTemplateNotFoundError, resolve_shop_app_config_template
from lib.static_scripts import (
    AppConfigScriptInUseError,
    delete_script,
    effective_app_config_script_name,
    get_script,
    list_app_config_scripts,
    list_scripts,
    parse_is_app_config_query,
    parse_static_script_put_payload,
    put_script,
    script_name_rules,
    validate_script_name,
)
from lib.storefront_gwofy_config import (
    config_layers_for_admin,
    merge_storefront_config_patch,
    normalize_storefront_config_for_storage,
    parse_script_config_overlay,
    parse_storefront_config_from_meta,
    should_remove_storefront_config_storage,
    derived_readonly_keys_in_patch,
    validate_storefront_config_patch,
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
from lib.shop_offline_access import (
    ShopAdminAuth,
    ShopifyAuth401Error,
    get_fresh_shop_access_token,
    retry_shop_offline_token_refresh,
    shopify_auth_401_response_body,
)
from lib.admin_shop_sync import normalize_resources, run_admin_shop_sync
from lib.shopify_api import DEFAULT_API_VERSION

logger = setup_logging("admin")

ddb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")


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

    if method == "POST" and path == "/admin/tools/decrypt-shopify-token":
        return _post_admin_decrypt_shopify_token(event, table, actor_sub, actor_email, req_id)

    static_parts = path.strip("/").split("/")
    if len(static_parts) >= 2 and static_parts[0] == "admin" and static_parts[1] == "static-scripts":
        return _handle_admin_static_scripts(
            method, static_parts, event, table, actor_sub, actor_email, req_id
        )

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
        return _list_products(event, table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "orders":
        return _list_orders(event, table, shop, actor_sub, req_id)
    if method == "GET" and len(parts) == 4 and parts[3] == "audit":
        return _list_audit(event, table, shop, actor_sub, req_id)

    if method == "PUT" and len(parts) == 4 and parts[3] == "shipping-calc-settings":
        return _put_shipping_calc(event, table, shop, actor_sub, actor_email, req_id)

    if method == "POST" and len(parts) == 4 and parts[3] == "sync-enabled-currencies":
        return _post_admin_sync_shop_currencies(event, table, shop, actor_sub, actor_email, req_id)

    if method == "POST" and len(parts) == 4 and parts[3] == "retry-offline-token":
        return _post_admin_retry_offline_token(table, shop, actor_sub, actor_email, req_id)

    if method == "POST" and len(parts) == 4 and parts[3] == "sync":
        return _post_admin_shop_sync(event, table, shop, actor_sub, actor_email, req_id)

    if method == "GET" and len(parts) == 4 and parts[3] == "calc-coverage-tips":
        return _get_shop_calc_coverage_tips(table, shop, actor_sub, req_id)

    if method == "PUT" and len(parts) == 4 and parts[3] == "calc-coverage-tips":
        return _put_shop_calc_coverage_tips(event, table, shop, actor_sub, actor_email, req_id)

    if method == "GET" and len(parts) == 4 and parts[3] == "storefront-config":
        return _get_shop_storefront_config(table, shop, actor_sub, req_id)

    if method == "PUT" and len(parts) == 4 and parts[3] == "storefront-config":
        return _put_shop_storefront_config(event, table, shop, actor_sub, actor_email, req_id)

    if method == "POST" and len(parts) == 5 and parts[3] == "features" and parts[4] == "return-insurance":
        return _feature_return(event, table, shop, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 5 and parts[3] == "features" and parts[4] == "shipping-protection":
        return _feature_shipping(event, table, shop, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 4 and parts[3] == "suspend":
        return _suspend(table, shop, True, actor_sub, actor_email, req_id)
    if method == "POST" and len(parts) == 4 and parts[3] == "resume":
        return _suspend(table, shop, False, actor_sub, actor_email, req_id)

    return _resp(404, {"error": "not_found"})


_MAX_ACCESS_TOKEN_ENC_B64 = 24_000


def _post_admin_decrypt_shopify_token(
    event, table, actor_sub: str, actor_email: str, req_id: str
):
    """Decrypt Dynamo `access_token_enc` (KMS + same context as oauth). Admin-only; audit without plaintext."""
    try:
        raw = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    if not isinstance(raw, dict):
        return _resp(400, {"error": "invalid_body"})
    enc = raw.get("access_token_enc")
    if not isinstance(enc, str) or not enc.strip():
        return _resp(400, {"error": "access_token_enc_required"})
    enc = enc.strip()
    if len(enc) > _MAX_ACCESS_TOKEN_ENC_B64:
        return _resp(400, {"error": "access_token_enc_too_large"})

    kms_key_id = str(raw.get("kms_key_id") or os.environ.get("KMS_KEY_ID") or "").strip()
    if not kms_key_id:
        return _resp(
            500,
            {
                "error": "kms_key_id_missing",
                "hint": "Admin Lambda needs KMS_KEY_ID or pass kms_key_id in JSON body",
            },
        )

    shop_hint = raw.get("shop")
    shop_audit = (
        str(shop_hint).strip().lower().rstrip("/")
        if isinstance(shop_hint, str) and shop_hint.strip()
        else "gwofy-internal-audit"
    )

    def _audit(outcome: str, detail: dict[str, Any]) -> None:
        try:
            append_audit(
                table,
                shop_audit,
                actor_type="admin",
                actor_id=actor_sub,
                action="ADMIN_DECRYPT_SHOPIFY_TOKEN",
                outcome=outcome,
                resource="kms",
                detail=detail,
                http_path="/admin/tools/decrypt-shopify-token",
                request_id=req_id,
                actor_email=actor_email or None,
            )
        except Exception:
            logger.exception("admin_decrypt_audit_append_failed")

    try:
        plain = decrypt_token(kms_key_id, enc)
    except Exception as e:
        logger.warning(
            "admin_decrypt_shopify_token_failed",
            extra={"sub": actor_sub, "detail": str(e)[:300]},
        )
        _audit(
            "error",
            {
                "ciphertext_b64_len": len(enc),
                "kms_key_id_suffix": kms_key_id[-32:],
                "error": str(e)[:400],
            },
        )
        return _resp(502, {"error": "decrypt_failed", "detail": str(e)[:400]})

    _audit(
        "ok",
        {
            "ciphertext_b64_len": len(enc),
            "plaintext_len": len(plain),
            "kms_key_id_suffix": kms_key_id[-32:],
        },
    )
    return _resp(200, {"ok": True, "access_token": plain})


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


_MISSING = object()


def _storefront_config_response(table, meta: dict, shop: str) -> dict[str, Any]:
    script_overlay: dict[str, Any] = {}
    try:
        template, _ = resolve_shop_app_config_template(table, meta)
        script_overlay = parse_script_config_overlay(template)
    except AppConfigTemplateNotFoundError:
        pass
    layers = config_layers_for_admin(
        table,
        meta,
        shop,
        storefront_js_version=APP_STOREFRONT_VERSION,
        script_overlay=script_overlay,
    )
    layers["appConfigScriptName"] = effective_app_config_script_name(meta)
    layers["appConfigScripts"] = list_app_config_scripts(table)
    return layers


def _validate_app_config_script_binding(table, name: str) -> str | None:
    try:
        safe = validate_script_name(name)
    except ValueError:
        return "invalid_app_config_script_name"
    script = get_script(table, safe)
    if not script or not script.get("isAppConfig"):
        return "invalid_app_config_script"
    return None


def _get_shop_storefront_config(table, shop: str, actor_sub: str, req_id: str):
    pk = pk_shop(shop)
    meta = table.get_item(Key={"pk": pk, "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_STOREFRONT_CONFIG_READ",
        outcome="ok",
        resource="storefront_config",
        http_path=f"/admin/shops/{shop}/storefront-config",
        request_id=req_id,
    )
    return _resp(200, _storefront_config_response(table, meta, shop))


def _put_shop_storefront_config(event, table, shop: str, actor_sub: str, actor_email: str, req_id: str):
    pk = pk_shop(shop)
    meta = table.get_item(Key={"pk": pk, "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    if not isinstance(body, dict):
        return _resp(400, {"error": "body_must_be_object"})

    app_script_update = body.get("appConfigScriptName", _MISSING)
    config_body = {k: v for k, v in body.items() if k != "appConfigScriptName"}

    if config_body:
        patch, verr = validate_storefront_config_patch(config_body)
    else:
        patch, verr = {}, None

    if verr:
        err_body: dict = {"error": "invalid_storefront_config"}
        if verr == "derived_readonly_keys":
            err_body["detail"] = derived_readonly_keys_in_patch(config_body)
        else:
            err_body["detail"] = verr
        return _resp(400, err_body)

    if app_script_update is not _MISSING:
        if app_script_update is not None and (
            not isinstance(app_script_update, str) or not app_script_update.strip()
        ):
            return _resp(400, {"error": "invalid_app_config_script_name"})
        if app_script_update is not None:
            bind_err = _validate_app_config_script_binding(table, app_script_update.strip())
            if bind_err:
                return _resp(400, {"error": bind_err, "name": app_script_update.strip()})
    elif not patch:
        return _resp(400, {"error": "invalid_storefront_config", "detail": "no_fields_to_update"})

    now = datetime.now(timezone.utc).isoformat()
    expr_parts: list[str] = ["updated_at = :u"]
    expr_names: dict[str, str] = {}
    expr_vals: dict[str, Any] = {":u": now}
    removes: list[str] = []

    if patch:
        existing = parse_storefront_config_from_meta(meta)
        merged = merge_storefront_config_patch(existing, patch)
        if should_remove_storefront_config_storage(merged):
            removes.append("#sc")
            expr_names["#sc"] = STOREFRONT_CONFIG_JSON
        else:
            expr_names["#sc"] = STOREFRONT_CONFIG_JSON
            expr_vals[":sc"] = normalize_storefront_config_for_storage(merged)
            expr_parts.append("#sc = :sc")

    if app_script_update is not _MISSING:
        expr_names["#acn"] = APP_CONFIG_SCRIPT_NAME
        if app_script_update is None:
            removes.append("#acn")
        else:
            expr_vals[":acn"] = validate_script_name(str(app_script_update).strip())
            expr_parts.append("#acn = :acn")

    update_expr = "SET " + ", ".join(expr_parts)
    if removes:
        update_expr = "REMOVE " + ", ".join(removes) + " " + update_expr

    update_kwargs: dict[str, Any] = {
        "Key": {"pk": pk, "sk": SK_METADATA},
        "UpdateExpression": update_expr,
        "ExpressionAttributeValues": expr_vals,
    }
    if expr_names:
        update_kwargs["ExpressionAttributeNames"] = expr_names
    table.update_item(**update_kwargs)

    audit_detail: dict[str, Any] = {}
    if patch:
        audit_detail["patch_keys"] = sorted(patch.keys())
    if app_script_update is not _MISSING:
        audit_detail["appConfigScriptName"] = (
            None if app_script_update is None else validate_script_name(str(app_script_update).strip())
        )

    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_STOREFRONT_CONFIG_UPDATE",
        outcome="ok",
        resource="storefront_config",
        actor_email=actor_email or None,
        detail=audit_detail,
        http_path=f"/admin/shops/{shop}/storefront-config",
        request_id=req_id,
    )
    refreshed = table.get_item(Key={"pk": pk, "sk": SK_METADATA}).get("Item") or meta
    return _resp(200, {"ok": True, **_storefront_config_response(table, refreshed, shop)})


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


def _post_admin_retry_offline_token(
    table, shop: str, actor_sub: str, actor_email: str, req_id: str
):
    """POST /admin/shops/{shop}/retry-offline-token — force OAuth refresh, restore ACTIVE when valid."""
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    if not meta.get("access_token_enc"):
        return _resp(400, {"error": "missing_access_token"})
    kms_key_id = os.environ["KMS_KEY_ID"]
    try:
        result = retry_shop_offline_token_refresh(
            table,
            shop,
            kms_key_id_fallback=kms_key_id,
            client_id=os.environ["SHOPIFY_CLIENT_ID"],
            client_secret=os.environ["SHOPIFY_CLIENT_SECRET"],
        )
    except ShopifyAuth401Error as e:
        return _resp(401, shopify_auth_401_response_body(e, meta))
    except Exception as e:
        return _resp(500, {"error": "retry_offline_token_failed", "detail": str(e)[:400]})
    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_RETRY_OFFLINE_TOKEN",
        outcome="ok",
        actor_email=actor_email or None,
        detail=result,
        http_path=f"/admin/shops/{shop}/retry-offline-token",
        request_id=req_id,
    )
    return _resp(200, result)


def _post_admin_sync_shop_currencies(event, table, shop: str, actor_sub: str, actor_email: str, req_id: str):
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    enc = meta.get("access_token_enc")
    if not enc:
        return _resp(400, {"error": "missing_access_token"})
    kms_key_id = os.environ["KMS_KEY_ID"]
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    auth = ShopAdminAuth(
        table,
        shop,
        kms_key_id,
        os.environ["SHOPIFY_CLIENT_ID"],
        os.environ["SHOPIFY_CLIENT_SECRET"],
        api_version,
        _meta=meta,
    )
    try:
        token = auth.access_token()
    except ShopifyAuth401Error as e:
        return _resp(401, shopify_auth_401_response_body(e, meta))
    except Exception as e:
        return _resp(500, {"error": "token_resolve_failed", "detail": str(e)[:400]})
    fb = str(meta.get("shop_currency_code") or "").strip().upper()
    try:
        codes = sync_shop_enabled_currencies(
            table, shop, token, api_version, fallback_primary=fb or None, auth=auth
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


def _post_admin_shop_sync(
    event, table, shop: str, actor_sub: str, actor_email: str, req_id: str
):
    """
    POST /admin/shops/{shop}/sync
    Body: {
      "resources": ["all"] | ["shop_profile","products","orders","currencies","markets","catalog"],
      "async": true,           // default true — enqueue Worker (recommended for products/orders)
      "reset_checkpoints": false  // if true, restart full product/order pagination from scratch
    }
    """
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    if meta.get("installation_status") != "ACTIVE":
        return _resp(400, {"error": "shop_not_active"})
    if meta.get("plugin_suspended"):
        return _resp(403, {"error": "plugin_suspended"})
    enc = meta.get("access_token_enc")
    if not enc:
        return _resp(400, {"error": "missing_access_token"})
    store_number = str(meta.get("store_number") or "")
    if not store_number:
        return _resp(400, {"error": "missing_store_number"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})
    if not isinstance(body, dict):
        return _resp(400, {"error": "body_must_be_object"})

    raw_resources = body.get("resources")
    if raw_resources is None:
        raw_resources = ["all"]
    if isinstance(raw_resources, str):
        raw_resources = [raw_resources]
    if not isinstance(raw_resources, list) or not raw_resources:
        return _resp(400, {"error": "resources_required", "hint": 'e.g. ["all"] or ["products","orders"]'})

    try:
        normalized = normalize_resources([str(x) for x in raw_resources])
    except ValueError as e:
        code = str(e)
        if code.startswith("unknown_resource:"):
            return _resp(400, {"error": "unknown_resource", "detail": code.split(":", 1)[-1]})
        return _resp(400, {"error": "invalid_resources", "detail": code})

    reset_cp = bool(body.get("reset_checkpoints"))
    use_async = body.get("async")
    if use_async is None:
        use_async = True
    else:
        use_async = bool(use_async)

    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    http_path = f"/admin/shops/{shop}/sync"

    if use_async:
        q = os.environ.get("WORK_QUEUE_URL")
        if not q:
            return _resp(503, {"error": "work_queue_not_configured"})
        msg = {
            "source": "admin_api",
            "shop": shop,
            "store_number": store_number,
            "resources": normalized,
            "reset_checkpoints": reset_cp,
            "api_version": api_version,
            "requested_by": actor_sub,
        }
        r = sqs.send_message(QueueUrl=q, MessageBody=json.dumps(msg))
        append_audit(
            table,
            shop,
            actor_type="admin",
            actor_id=actor_sub,
            action="ADMIN_SHOP_SYNC_ENQUEUE",
            outcome="ok",
            resource=",".join(normalized),
            actor_email=actor_email or None,
            detail={"async": True, "reset_checkpoints": reset_cp},
            http_path=http_path,
            request_id=req_id,
        )
        return _resp(
            202,
            {
                "ok": True,
                "async": True,
                "shop": shop,
                "resources": normalized,
                "reset_checkpoints": reset_cp,
                "sqs_message_id": r.get("MessageId"),
            },
        )

    kms_key_id = os.environ["KMS_KEY_ID"]
    try:
        token = get_fresh_shop_access_token(
            table,
            shop,
            kms_key_id_fallback=kms_key_id,
            client_id=os.environ["SHOPIFY_CLIENT_ID"],
            client_secret=os.environ["SHOPIFY_CLIENT_SECRET"],
            meta=meta,
        )
    except Exception as e:
        return _resp(500, {"error": "token_resolve_failed", "detail": str(e)[:400]})

    try:
        result = run_admin_shop_sync(
            table,
            shop,
            store_number,
            token,
            kms_key_id,
            api_version,
            normalized,
            reset_checkpoints=reset_cp,
        )
    except ValueError as e:
        err = str(e)
        if err == "shop_not_active":
            return _resp(400, {"error": err})
        if err == "missing_access_token":
            return _resp(400, {"error": err})
        return _resp(400, {"error": "sync_failed", "detail": err})

    outcome = "ok" if result.get("ok") else "partial_error"
    append_audit(
        table,
        shop,
        actor_type="admin",
        actor_id=actor_sub,
        action="ADMIN_SHOP_SYNC",
        outcome=outcome,
        resource=",".join(normalized),
        actor_email=actor_email or None,
        detail={"async": False, "steps": list((result.get("steps") or {}).keys())},
        http_path=http_path,
        request_id=req_id,
    )
    status = 200 if result.get("ok") else 502
    return _resp(status, {**result, "async": False})


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
                "main_theme_gid": it.get("main_theme_gid"),
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


def _is_primary_product_mirror_sk(sk: str) -> bool:
    """Main product snapshot rows only (exclude PRODUCT#...#META_VER# history)."""
    s = str(sk or "")
    return s.startswith("PRODUCT#") and "#META_VER#" not in s


def _decode_list_cursor(cursor_b64: str) -> tuple[dict[str, Any] | None, str | None]:
    if not cursor_b64:
        return None, None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor_b64.encode("ascii")).decode("utf-8")), None
    except Exception:
        return None, "invalid_cursor"


def _encode_list_cursor(lek: dict[str, Any] | None) -> str | None:
    if not lek:
        return None
    return base64.urlsafe_b64encode(json.dumps(lek, default=str).encode("utf-8")).decode("ascii")


def _iso8601_query_value(qs: dict[str, Any], key: str) -> tuple[str | None, str | None]:
    raw = (qs.get(key) or "").strip()
    if not raw:
        return None, None
    try:
        normalized = raw.replace("Z", "+00:00")
        datetime.fromisoformat(normalized)
    except ValueError:
        return None, key
    return raw, None


def _query_tenant_mirror_page(
    table,
    *,
    pk_t: str,
    sk_prefix: str,
    limit: int,
    exclusive_start_key: dict[str, Any] | None,
    filt: list[str],
    names: dict[str, str],
    vals: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": "pk = :p AND begins_with(sk, :pre)",
        "ExpressionAttributeValues": {**vals, ":p": pk_t, ":pre": sk_prefix},
        "Limit": limit,
    }
    if exclusive_start_key:
        kwargs["ExclusiveStartKey"] = exclusive_start_key
    if filt:
        kwargs["FilterExpression"] = " AND ".join(filt)
    if names:
        kwargs["ExpressionAttributeNames"] = names
    resp = table.query(**kwargs)
    return resp.get("Items", []), resp.get("LastEvaluatedKey")


def _tenant_mirror_list_response(
    table,
    *,
    pk_t: str,
    sk_prefix: str,
    limit: int,
    cursor_b64: str,
    filt: list[str],
    names: dict[str, str],
    vals: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    eks, err = _decode_list_cursor(cursor_b64)
    if err:
        return [], None, err
    items, lek = _query_tenant_mirror_page(
        table,
        pk_t=pk_t,
        sk_prefix=sk_prefix,
        limit=limit,
        exclusive_start_key=eks,
        filt=filt,
        names=names,
        vals=vals,
    )
    return items, _encode_list_cursor(lek), None


def _list_products(event, table, shop: str, actor_sub: str, req_id: str):
    qs = event.get("queryStringParameters") or {}
    include_deleted = (qs.get("include_deleted") or "").lower() in ("1", "true", "yes")
    limit = min(int(qs.get("limit") or 100), 500)
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    sn = str(meta.get("store_number", ""))
    pk_t = pk_tenant(sn)
    names: dict[str, str] = {}
    vals: dict[str, Any] = {}
    filt: list[str] = ["attribute_exists(product_handle)"]
    if not include_deleted:
        names["#sd"] = "sync_deleted"
        filt.append("(attribute_not_exists(#sd) OR #sd = :fv)")
        vals[":fv"] = False
    if prefix := (qs.get("product_handle_prefix") or "").strip():
        filt.append("begins_with(product_handle, :hpre)")
        vals[":hpre"] = prefix
    if title_prefix := (qs.get("product_title_prefix") or "").strip():
        filt.append("begins_with(product_title, :tpre)")
        vals[":tpre"] = title_prefix
    if pst := (qs.get("product_status") or "").strip():
        filt.append("product_status = :pst")
        vals[":pst"] = pst.upper()
    if sku := (qs.get("sku") or "").strip():
        filt.append("contains(variant_skus, :vsku)")
        vals[":vsku"] = sku
    if asin := (qs.get("asin") or qs.get("barcode") or "").strip():
        filt.append("contains(variant_barcodes, :vbc)")
        vals[":vbc"] = asin
    items, next_cursor, err = _tenant_mirror_list_response(
        table,
        pk_t=pk_t,
        sk_prefix="PRODUCT#",
        limit=limit,
        cursor_b64=qs.get("cursor") or "",
        filt=filt,
        names=names,
        vals=vals,
    )
    if err:
        return _resp(400, {"error": err})
    return _resp(200, {"items": items, "next_cursor": next_cursor})


def _list_orders(event, table, shop: str, actor_sub: str, req_id: str):
    qs = event.get("queryStringParameters") or {}
    only_prot = (qs.get("only_protection") or qs.get("has_shipping_protection") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    include_deleted = (qs.get("include_deleted") or "").lower() in ("1", "true", "yes")
    meta = table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")
    if not meta:
        return _resp(404, {"error": "not_found"})
    sn = str(meta.get("store_number", ""))
    pk_t = pk_tenant(sn)
    names: dict[str, str] = {}
    vals: dict[str, Any] = {}
    filt: list[str] = []
    if not include_deleted:
        names["#sd"] = "sync_deleted"
        filt.append("(attribute_not_exists(#sd) OR #sd = :fv)")
        vals[":fv"] = False
    if only_prot:
        filt.append("has_shipping_protection = :hp")
        vals[":hp"] = True
    if fs := (qs.get("financial_status") or "").strip():
        filt.append("display_financial_status = :dfs")
        vals[":dfs"] = fs.upper()
    if ffs := (qs.get("fulfillment_status") or "").strip():
        filt.append("display_fulfillment_status = :ffs")
        vals[":ffs"] = ffs.upper()
    if on := (qs.get("order_name") or "").strip():
        filt.append("order_name = :on")
        vals[":on"] = on
    elif onp := (qs.get("order_name_prefix") or "").strip():
        filt.append("begins_with(order_name, :onp)")
        vals[":onp"] = onp
    if lrid := (qs.get("legacy_resource_id") or qs.get("order_number") or "").strip():
        filt.append("legacy_resource_id = :lrid")
        vals[":lrid"] = lrid
    if tag_filter := (qs.get("tag") or "").strip():
        filt.append("contains(sync_tags, :tg)")
        vals[":tg"] = tag_filter
    if sku := (qs.get("sku") or "").strip():
        filt.append("contains(line_item_skus, :lsku)")
        vals[":lsku"] = sku
    for key, attr, op in (
        ("created_from", "order_created_at", ">="),
        ("created_to", "order_created_at", "<="),
        ("processed_from", "order_processed_at", ">="),
        ("processed_to", "order_processed_at", "<="),
        ("updated_from", "updated_at_source", ">="),
        ("updated_to", "updated_at_source", "<="),
    ):
        raw, bad = _iso8601_query_value(qs, key)
        if bad:
            return _resp(400, {"error": "invalid_date", "field": bad})
        if raw:
            placeholder = f":{key.replace('_', '')}"
            filt.append(f"{attr} {op} {placeholder}")
            vals[placeholder] = raw
    limit = min(int(qs.get("limit") or 100), 500)
    items, next_cursor, err = _tenant_mirror_list_response(
        table,
        pk_t=pk_t,
        sk_prefix="ORDER#",
        limit=limit,
        cursor_b64=qs.get("cursor") or "",
        filt=filt,
        names=names,
        vals=vals,
    )
    if err:
        return _resp(400, {"error": err})
    return _resp(200, {"items": items, "next_cursor": next_cursor})


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


def _handle_admin_static_scripts(
    method: str,
    parts: list[str],
    event,
    table,
    actor_sub: str,
    actor_email: str,
    req_id: str,
):
    if method == "GET" and len(parts) == 2:
        qs = event.get("queryStringParameters") or {}
        is_app_config_filter, qerr = parse_is_app_config_query(qs if isinstance(qs, dict) else None)
        if qerr:
            return _resp(400, {"error": qerr})
        body: dict[str, Any] = {
            "scripts": list_scripts(table, is_app_config=is_app_config_filter),
            "nameRules": script_name_rules(),
        }
        if is_app_config_filter is not None:
            body["isAppConfigFilter"] = is_app_config_filter
        return _resp(200, body)

    if len(parts) != 3:
        return _resp(404, {"error": "not_found"})

    name = unquote(parts[2]).strip()
    http_path = f"/admin/static-scripts/{name}"

    try:
        validate_script_name(name)
    except ValueError as e:
        return _resp(
            400,
            {
                "error": "invalid_script_name",
                "detail": str(e),
                "nameRules": script_name_rules(),
            },
        )

    if method == "GET":
        detail = get_script(table, name)
        if not detail:
            return _resp(404, {"error": "not_found", "name": name})
        return _resp(200, detail)

    if method == "DELETE":
        try:
            deleted = delete_script(table, name)
        except AppConfigScriptInUseError as e:
            return _resp(
                409,
                {
                    "error": "app_config_script_in_use",
                    "name": name,
                    "boundShops": e.bound_shops,
                },
            )
        if not deleted:
            return _resp(404, {"error": "not_found", "name": name})
        append_audit(
            table,
            "_gwofy_system_",
            actor_type="admin",
            actor_id=actor_sub,
            action="ADMIN_STATIC_SCRIPT_DELETE",
            outcome="ok",
            resource="static_script",
            actor_email=actor_email or None,
            detail={"name": name},
            http_path=http_path,
            request_id=req_id,
        )
        return _resp(200, {"ok": True, "name": name})

    if method == "PUT":
        try:
            source, confirm, is_app_config = parse_static_script_put_payload(event)
        except ValueError as e:
            code = str(e)
            if code in ("invalid_json", "body_must_be_object", "source_or_sourceBase64_required"):
                return _resp(400, {"error": code})
            if code == "invalid_sourceBase64":
                return _resp(400, {"error": code})
            return _resp(400, {"error": "invalid_static_script", "detail": code})
        try:
            created, detail = put_script(
                table,
                name,
                source,
                updated_by=actor_sub,
                confirm_overwrite=confirm,
                is_app_config=is_app_config,
            )
        except FileExistsError:
            return _resp(
                409,
                {
                    "error": "name_exists",
                    "name": name,
                    "hint": "Retry with confirmOverwrite: true to replace the existing script.",
                },
            )
        except ValueError as e:
            code = str(e)
            if code.startswith("script_name_"):
                return _resp(
                    400,
                    {
                        "error": "invalid_script_name",
                        "detail": code,
                        "nameRules": script_name_rules(),
                    },
                )
            if code in (
                "app_config_missing_gwofy_config",
                "app_config_missing_gwofy_config_assignment",
                "app_config_missing_inject_marker",
            ):
                return _resp(400, {"error": "invalid_app_config", "detail": code})
            return _resp(400, {"error": "invalid_static_script", "detail": code})
        action = "ADMIN_STATIC_SCRIPT_CREATE" if created else "ADMIN_STATIC_SCRIPT_UPDATE"
        append_audit(
            table,
            "_gwofy_system_",
            actor_type="admin",
            actor_id=actor_sub,
            action=action,
            outcome="ok",
            resource="static_script",
            actor_email=actor_email or None,
            detail={"name": detail["name"], "byteLength": detail.get("byteLength")},
            http_path=http_path,
            request_id=req_id,
        )
        return _resp(201 if created else 200, {"ok": True, **detail})

    return _resp(404, {"error": "not_found"})


def _resp(code: int, body: dict):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }

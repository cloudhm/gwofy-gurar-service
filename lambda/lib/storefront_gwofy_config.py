"""Storefront GWOFY_CONFIG: defaults, Dynamo-derived fields, per-shop overrides, JS bundle."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .default_protection_tiers import PROTECTION_PRODUCT_HANDLE
from .models import META_PROTECTION_PRODUCT_HANDLE, STOREFRONT_CONFIG_JSON, pk_tenant
from .shop_enabled_currencies import shop_supported_currencies_list
from .tips_config import get_tips_info

_CONFIG_INJECT_MARKER = "/*__GWOFY_CONFIG_JSON__*/"
_KERNEL_PATH = Path(__file__).resolve().parent.parent / "static" / "app-config.kernel.js"
_DEFAULTS_PATH = Path(__file__).resolve().parent / "storefront_gwofy_defaults.json"

# PATCH keys allowed in storefront_config_json (admin). Derived/read-only keys rejected on PUT.
_PATCH_TOP_KEYS = frozenset(
    {
        "debug",
        "isCartDefaultOpen",
        "shopifyPluginVersion",
        "remoteScriptUrls",
        "widgetThemeStyle",
        "spDisableCheck",
        "auth",
        "assets",
        "tipsDialogAssets",
        "widgetAssets",
        "pricing",
        "text",
        "copy",
        "spVersion",
        "supportedLocales",
        "tipsDialog",
        "themeSelectors",
        "styles",
    }
)
_PATCH_PRICING_KEYS = frozenset(
    {
        "calcRate",
        "hardMaxAmount",
        "spMaxCoverage",
        "spMinCoverage",
        "spBelowMinCoverageTip",
        "spGreaterMaxCoverAgeTip",
    }
)
_DERIVED_READONLY_PATHS = frozenset(
    {
        "shopId",
        "productHandle",
        "supportedCurrencies",
        "auth.isOpenForSP",
    }
)

_MAX_STRING_LEN = 16_384
_MAX_URL_LEN = 2048
_MAX_REMOTE_SCRIPTS = 8
_DEFAULT_CALC_RATE = "0.04"
# Keep in sync with static_assets.APP_STOREFRONT_VERSION when not passing version explicitly.
_DEFAULT_STOREFRONT_JS_VERSION = "1.0.0"
DEFAULT_REMOTE_SCRIPT_URLS = ["https://sp-prod.gwofy.com/static/app-storefront.js"]


def normalize_shop_host(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if not s:
        return None
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    s = s.split("/")[0].strip()
    return s or None


def is_valid_shop_host(host: str | None) -> bool:
    if not host:
        return False
    return "myshopify.com" in host


def shop_query_from_event(event: dict[str, Any]) -> str | None:
    qs = event.get("queryStringParameters") or {}
    if not isinstance(qs, dict):
        return None
    raw = qs.get("shop") or qs.get("shopId")
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return normalize_shop_host(str(raw) if raw is not None else None)


def default_gwofy_config() -> dict[str, Any]:
    data = json.loads(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid storefront_gwofy_defaults.json")
    out = copy.deepcopy(data)
    pricing = out.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
        out["pricing"] = pricing
    pricing.setdefault("calcRate", _DEFAULT_CALC_RATE)
    return out


def parse_storefront_config_from_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return {}
    raw = meta.get(STOREFRONT_CONFIG_JSON)
    if not raw:
        return {}
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
    if not isinstance(data, dict):
        return {}
    return copy.deepcopy(data)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in overlay.items():
        if val is None:
            out.pop(key, None)
            continue
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def effective_protection_product_handle(table, meta: dict[str, Any]) -> str:
    """
    自动插入的 Shipping Protection 商品 handle（不可经 storefront-config 覆盖）。
    优先 METADATA.protection_product_handle，其次商品镜像 payload，最后模板常量。
    """
    stored = str(meta.get(META_PROTECTION_PRODUCT_HANDLE) or "").strip()
    if stored:
        return stored
    gid = str(meta.get("protection_product_gid") or "").strip()
    sn = str(meta.get("store_number") or "").strip()
    if gid and sn and table is not None:
        item = table.get_item(Key={"pk": pk_tenant(sn), "sk": f"PRODUCT#{gid}"}).get("Item")
        if item:
            raw = item.get("payload")
            snap: dict[str, Any] | None = None
            if isinstance(raw, str):
                try:
                    snap = json.loads(raw)
                except json.JSONDecodeError:
                    snap = None
            elif isinstance(raw, dict):
                snap = raw
            if isinstance(snap, dict):
                h = str(snap.get("handle") or "").strip()
                if h:
                    return h
    return PROTECTION_PRODUCT_HANDLE


def shipping_protection_open_audited(meta: dict[str, Any]) -> bool:
    """SP 店面授权：仅 OPEN_AUDITED 视为开启（不可经 storefront-config 覆盖）。"""
    return str(meta.get("shipping_protection_status") or "CLOSED") == "OPEN_AUDITED"


def derived_from_meta(table, meta: dict[str, Any], shop_host: str) -> dict[str, Any]:
    sp_st = str(meta.get("shipping_protection_status") or "CLOSED")
    sp_open = sp_st.startswith("OPEN")
    sp_open_audited = shipping_protection_open_audited(meta)
    tips = get_tips_info(table)
    sp_ver = tips.get("spVersion") if isinstance(tips.get("spVersion"), dict) else {}

    supported = shop_supported_currencies_list(meta)

    debug = os.environ.get("STOREFRONT_DEBUG", "").strip().lower() in ("1", "true", "yes")

    return {
        "shopId": shop_host,
        "debug": debug,
        "auth": {"isOpenForSP": sp_open_audited},
        "isCartDefaultOpen": sp_open,
        "productHandle": effective_protection_product_handle(table, meta),
        "supportedCurrencies": supported,
        "spVersion": {
            "faqUrl": sp_ver.get("faqUrl", "javascript:void(0);"),
            "terms": sp_ver.get("terms", "javascript:void(0);"),
            "popup": sp_ver.get("popup", "SP-DL-5"),
        },
    }


def is_storefront_asset_url(url: str) -> bool:
    path = url.split("?", 1)[0]
    return path.endswith("/static/app-storefront.js")


def default_storefront_asset_url(*, storefront_js_version: str = _DEFAULT_STOREFRONT_JS_VERSION) -> str | None:
    base = storefront_asset_base_url()
    if not base:
        return None
    return f"{base}/static/app-storefront.js?v={storefront_js_version}"


def shop_override_has_remote_script_urls(meta: dict[str, Any] | None) -> bool:
    """True when storefront_config_json explicitly sets remoteScriptUrls."""
    if not meta:
        return False
    raw = meta.get(STOREFRONT_CONFIG_JSON)
    if not raw:
        return False
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return False
    if not isinstance(data, dict):
        return False
    return "remoteScriptUrls" in data


def apply_default_remote_script_urls_if_needed(
    merged: dict[str, Any],
    meta: dict[str, Any] | None,
) -> dict[str, Any]:
    if shop_override_has_remote_script_urls(meta):
        return merged
    out = copy.deepcopy(merged)
    out["remoteScriptUrls"] = list(DEFAULT_REMOTE_SCRIPT_URLS)
    return out


def build_effective_gwofy_config(
    table,
    meta: dict[str, Any],
    shop_host: str,
    *,
    storefront_js_version: str = _DEFAULT_STOREFRONT_JS_VERSION,
) -> dict[str, Any]:
    _ = storefront_js_version
    base = default_gwofy_config()
    derived = derived_from_meta(table, meta, shop_host)
    override = parse_storefront_config_from_meta(meta)
    merged = _deep_merge(base, derived)
    merged = _deep_merge(merged, override)
    return apply_default_remote_script_urls_if_needed(merged, meta)


def _flatten_paths(obj: dict[str, Any], prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            paths.update(_flatten_paths(v, path))
        else:
            paths.add(path)
    return paths


def derived_readonly_keys_in_patch(patch: dict[str, Any]) -> list[str]:
    paths = _flatten_paths(patch)
    bad = [p for p in paths if p in _DERIVED_READONLY_PATHS]
    if "shopId" in patch:
        bad.append("shopId")
    if "productHandle" in patch:
        bad.append("productHandle")
    if "supportedCurrencies" in patch:
        bad.append("supportedCurrencies")
    auth = patch.get("auth")
    if isinstance(auth, dict) and "isOpenForSP" in auth:
        bad.append("auth.isOpenForSP")
    return sorted(set(bad))


def _validate_url(s: str, field: str) -> None:
    if len(s) > _MAX_URL_LEN:
        raise ValueError(f"{field}_too_long")
    if s.startswith("javascript:") or s.startswith("//") or s.startswith("http://") or s.startswith("https://") or s.startswith("data:"):
        return
    raise ValueError(f"{field}_invalid_url")


def _validate_string(v: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(v, str):
        if v is None:
            v = ""
        else:
            v = str(v)
    if not allow_empty and not v.strip():
        raise ValueError(f"{field}_empty")
    if len(v) > _MAX_STRING_LEN:
        raise ValueError(f"{field}_too_long")
    return v


def _validate_calc_rate(v: Any) -> str:
    s = _validate_string(str(v), "calcRate")
    try:
        rate = float(s)
    except (TypeError, ValueError):
        raise ValueError("calcRate_invalid") from None
    if rate < 0 or rate > 1:
        raise ValueError("calcRate_out_of_range")
    return s


def _validate_amount_field(v: Any, field: str) -> str:
    s = _validate_string(str(v), field, allow_empty=True)
    if not s:
        return s
    try:
        amt = float(s)
    except (TypeError, ValueError):
        raise ValueError(f"{field}_invalid") from None
    if amt < 0:
        raise ValueError(f"{field}_must_be_non_negative")
    return s


def _validate_pricing_patch(pricing: dict[str, Any]) -> dict[str, Any]:
    p_unknown = set(pricing.keys()) - _PATCH_PRICING_KEYS
    if p_unknown:
        raise ValueError("invalid_pricing_keys")
    p_out: dict[str, Any] = {}
    for pk, pv in pricing.items():
        if pv is None:
            p_out[pk] = None
            continue
        if pk == "calcRate":
            p_out[pk] = _validate_calc_rate(pv)
        elif pk in ("hardMaxAmount", "spMaxCoverage", "spMinCoverage"):
            p_out[pk] = _validate_amount_field(pv, pk)
        elif pk in ("spBelowMinCoverageTip", "spGreaterMaxCoverAgeTip"):
            p_out[pk] = _validate_string(str(pv), pk, allow_empty=True)
        else:
            raise ValueError("invalid_pricing_keys")
    return p_out


def _validate_patch_value(key: str, val: Any) -> Any:
    if val is None:
        return None
    if key == "debug" or key == "spDisableCheck":
        if not isinstance(val, bool):
            raise ValueError(f"{key}_must_be_boolean")
        return val
    if key == "isCartDefaultOpen":
        if not isinstance(val, bool):
            raise ValueError("isCartDefaultOpen_must_be_boolean")
        return val
    if key == "shopifyPluginVersion":
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError("shopifyPluginVersion_must_be_integer")
        return val
    if key == "widgetThemeStyle":
        s = _validate_string(val, "widgetThemeStyle")
        if s not in ("white", "black"):
            raise ValueError("widgetThemeStyle_invalid")
        return s
    if key == "remoteScriptUrls":
        if isinstance(val, str):
            val = [val]
        if not isinstance(val, list):
            raise ValueError("remoteScriptUrls_must_be_array")
        if len(val) > _MAX_REMOTE_SCRIPTS:
            raise ValueError("remoteScriptUrls_too_many")
        out: list[str] = []
        for i, u in enumerate(val):
            su = _validate_string(u, f"remoteScriptUrls[{i}]")
            _validate_url(su, f"remoteScriptUrls[{i}]")
            out.append(su)
        return out
    if key == "supportedLocales":
        if not isinstance(val, list):
            raise ValueError("supportedLocales_must_be_array")
        return copy.deepcopy(val)
    if key in ("auth", "assets", "tipsDialogAssets", "widgetAssets", "text", "copy", "spVersion", "tipsDialog", "themeSelectors", "styles", "pricing"):
        if not isinstance(val, dict):
            raise ValueError(f"{key}_must_be_object")
        return copy.deepcopy(val)
    raise ValueError(f"unknown_key_{key}")


def _validate_patch_dict(body: dict[str, Any], *, allowed_top: frozenset[str]) -> dict[str, Any]:
    unknown = set(body.keys()) - allowed_top
    if unknown:
        raise ValueError("invalid_keys")
    out: dict[str, Any] = {}
    for key in body:
        if key == "pricing" and body[key] is not None:
            p = body[key]
            if not isinstance(p, dict):
                raise ValueError("pricing_must_be_object")
            out["pricing"] = _validate_pricing_patch(p)
            continue
        if key == "styles" and body[key] is not None:
            st = body[key]
            if not isinstance(st, dict):
                raise ValueError("styles_must_be_object")
            st_allowed = frozenset({"widgetExtra", "tipsDialogExtra"})
            if set(st.keys()) - st_allowed:
                raise ValueError("invalid_styles_keys")
            out["styles"] = {
                k: _validate_string(st[k], k, allow_empty=True) if st[k] is not None else None
                for k in st
                if k in st_allowed
            }
            continue
        out[key] = _validate_patch_value(key, body[key])
    return out


def validate_storefront_config_patch(body: Any) -> tuple[dict[str, Any], str | None]:
    if not isinstance(body, dict):
        return {}, "body_must_be_object"
    if not body:
        return {}, "no_fields_to_update"
    readonly = derived_readonly_keys_in_patch(body)
    if readonly:
        return {}, "derived_readonly_keys"
    try:
        patch = _validate_patch_dict(body, allowed_top=_PATCH_TOP_KEYS)
    except ValueError as e:
        return {}, str(e.args[0])
    if not patch:
        return {}, "no_fields_to_update"
    return patch, None


def merge_storefront_config_patch(
    existing: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(existing)
    for key, val in patch.items():
        if val is None:
            merged.pop(key, None)
            continue
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


def normalize_storefront_config_for_storage(config: dict[str, Any]) -> str:
    return json.dumps(config, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def should_remove_storefront_config_storage(config: dict[str, Any]) -> bool:
    return not config


def config_layers_for_admin(
    table,
    meta: dict[str, Any],
    shop_host: str,
    *,
    storefront_js_version: str = _DEFAULT_STOREFRONT_JS_VERSION,
) -> dict[str, Any]:
    defaults = apply_default_remote_script_urls_if_needed(default_gwofy_config(), None)
    derived = derived_from_meta(table, meta, shop_host)
    shop_override = parse_storefront_config_from_meta(meta)
    effective = build_effective_gwofy_config(
        table, meta, shop_host, storefront_js_version=storefront_js_version
    )
    return {
        "shop": shop_host,
        "defaults": defaults,
        "derived": derived,
        "shopOverride": shop_override,
        "effective": effective,
    }


def etag_for_app_config(
    kernel_version: str,
    shop_host: str,
    merged_config: dict[str, Any],
    updated_at: str,
) -> str:
    payload = json.dumps(
        {
            "v": kernel_version,
            "shop": shop_host,
            "config": merged_config,
            "updated_at": updated_at,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def storefront_asset_base_url() -> str:
    base = (os.environ.get("WEBHOOK_BASE_URL") or "").strip().rstrip("/")
    return base


def build_app_config_js(
    merged_config: dict[str, Any],
    *,
    kernel_version: str,
    storefront_js_version: str,
) -> str:
    kernel = _KERNEL_PATH.read_text(encoding="utf-8")
    if _CONFIG_INJECT_MARKER not in kernel:
        raise ValueError("app-config.kernel.js missing inject marker")
    config_json = json.dumps(merged_config, ensure_ascii=False)
    return kernel.replace(_CONFIG_INJECT_MARKER, config_json)

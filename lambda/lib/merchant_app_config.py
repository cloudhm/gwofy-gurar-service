"""Shop METADATA `merchant_app_config_json`: merchant app settings (couponCode, etc.)."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .models import MERCHANT_APP_CONFIG_JSON

MAX_COUPON_CODE_LEN = 64
_COUPON_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# API camelCase key -> validator returning stored value or None to remove; raises ValueError on invalid.
Validators = dict[str, Callable[[Any], str | None]]


def _validate_coupon_code(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("couponCode_must_be_string")
    s = v.strip()
    if not s:
        raise ValueError("couponCode_empty")
    if len(s) > MAX_COUPON_CODE_LEN or not _COUPON_CODE_RE.match(s):
        raise ValueError("couponCode_invalid")
    return s


ALLOWED_KEYS: Validators = {
    "couponCode": _validate_coupon_code,
}


def default_app_config() -> dict[str, Any]:
    return {}


def parse_app_config_from_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return default_app_config()
    raw = meta.get(MERCHANT_APP_CONFIG_JSON)
    if not raw:
        return default_app_config()
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return default_app_config()
    if not isinstance(data, dict):
        return default_app_config()
    out: dict[str, Any] = {}
    for key in ALLOWED_KEYS:
        if key in data and data[key] is not None:
            out[key] = data[key]
    return out


def validate_patch(body: Any) -> tuple[dict[str, str | None], str | None]:
    """Return mapping of key -> value (None = remove) or error code."""
    if not isinstance(body, dict):
        return {}, "body_must_be_object"
    if not body:
        return {}, "no_fields_to_update"
    unknown = set(body.keys()) - set(ALLOWED_KEYS.keys())
    if unknown:
        return {}, "invalid_keys"
    patch: dict[str, str | None] = {}
    for key, validator in ALLOWED_KEYS.items():
        if key not in body:
            continue
        try:
            patch[key] = validator(body[key])
        except ValueError as e:
            return {}, str(e.args[0])
    if not patch:
        return {}, "no_fields_to_update"
    return patch, None


def merge_app_config(existing: dict[str, Any], patch: dict[str, str | None]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def normalize_for_storage(config: dict[str, Any]) -> str:
    return json.dumps(config, separators=(",", ":"), sort_keys=True)


def should_remove_storage(config: dict[str, Any]) -> bool:
    return not config

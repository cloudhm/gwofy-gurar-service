"""Shop METADATA `merchant_premium_rules_json`: merchant-configured markup and promotions (config + serve only)."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import MERCHANT_PREMIUM_RULES_JSON
from .shipping_country_defaults import is_country_supported

MAX_PROMOTIONS = 20
MAX_MARKUP_PERCENT = 500.0
MAX_DISCOUNT_PERCENT = 100.0
MAX_FIXED_AMOUNT = 1_000_000.0
MAX_PROMO_ID_LEN = 64
_RULES_VERSION_MAX = 1000

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def default_rules() -> dict[str, Any]:
    return {
        "version": 1,
        "markup": {
            "default": {"addPercent": 0.0, "addFixed": 0.0},
            "byCountry": {},
        },
        "promotions": [],
        "promotionApplyMode": "highest_threshold_wins",
    }


def _float_nonneg(v: Any, field: str) -> tuple[float | None, str | None]:
    if v is None:
        return None, f"{field}_required"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None, f"{field}_invalid_number"
    if x < 0:
        return None, f"{field}_must_be_non_negative"
    return x, None


def validate_rules(table, body: Any) -> str | None:
    """Return error code string if invalid; None if ok."""
    if not isinstance(body, dict):
        return "body_must_be_object"
    try:
        ver = int(body.get("version", 1))
    except (TypeError, ValueError):
        return "invalid_version"
    if ver < 1 or ver > _RULES_VERSION_MAX:
        return "invalid_version"

    mk = body.get("markup")
    if not isinstance(mk, dict):
        return "markup_must_be_object"
    dfl = mk.get("default")
    if not isinstance(dfl, dict):
        return "markup_default_must_be_object"
    ap, err = _float_nonneg(dfl.get("addPercent"), "markup.default.addPercent")
    if err:
        return err
    assert ap is not None
    if ap > MAX_MARKUP_PERCENT:
        return "markup_default_add_percent_too_large"
    af, err = _float_nonneg(dfl.get("addFixed"), "markup.default.addFixed")
    if err:
        return err
    assert af is not None
    if af > MAX_FIXED_AMOUNT:
        return "markup_default_add_fixed_too_large"

    byc = mk.get("byCountry")
    if byc is None:
        pass
    elif not isinstance(byc, dict):
        return "markup_by_country_must_be_object"
    else:
        for k, row in byc.items():
            cc = str(k).strip().upper()
            if not is_country_supported(table, cc):
                return f"unsupported_country_in_markup:{cc}"
            if not isinstance(row, dict):
                return f"markup_by_country_{cc}_must_be_object"
            ap2, err = _float_nonneg(row.get("addPercent"), f"markup.byCountry.{cc}.addPercent")
            if err:
                return err
            assert ap2 is not None
            if ap2 > MAX_MARKUP_PERCENT:
                return "markup_country_add_percent_too_large"
            af2, err = _float_nonneg(row.get("addFixed"), f"markup.byCountry.{cc}.addFixed")
            if err:
                return err
            assert af2 is not None
            if af2 > MAX_FIXED_AMOUNT:
                return "markup_country_add_fixed_too_large"

    promos = body.get("promotions")
    if promos is None:
        promos = []
    if not isinstance(promos, list):
        return "promotions_must_be_array"
    if len(promos) > MAX_PROMOTIONS:
        return "too_many_promotions"

    seen_ids: set[str] = set()
    for i, p in enumerate(promos):
        if not isinstance(p, dict):
            return f"promotion_{i}_must_be_object"
        pid = p.get("id")
        if not isinstance(pid, str) or not pid.strip():
            return f"promotion_{i}_id_required"
        pid = pid.strip()
        if len(pid) > MAX_PROMO_ID_LEN or not _ID_RE.match(pid):
            return f"promotion_{i}_id_invalid"
        if pid in seen_ids:
            return f"promotion_duplicate_id:{pid}"
        seen_ids.add(pid)

        mcs, err = _float_nonneg(p.get("minCartSubtotal"), f"promotion_{i}.minCartSubtotal")
        if err:
            return err
        assert mcs is not None
        if mcs > MAX_FIXED_AMOUNT:
            return f"promotion_{i}_min_cart_too_large"

        dt = p.get("discountType")
        if dt not in ("percent", "fixed"):
            return f"promotion_{i}_invalid_discount_type"

        dv, err = _float_nonneg(p.get("discountValue"), f"promotion_{i}.discountValue")
        if err:
            return err
        assert dv is not None
        if dt == "percent" and dv > MAX_DISCOUNT_PERCENT:
            return f"promotion_{i}_discount_percent_too_large"
        if dt == "fixed" and dv > MAX_FIXED_AMOUNT:
            return f"promotion_{i}_discount_fixed_too_large"

        cty = p.get("country")
        if cty is not None and cty != "":
            if not isinstance(cty, str):
                return f"promotion_{i}_country_invalid"
            cc = cty.strip().upper()
            if not is_country_supported(table, cc):
                return f"promotion_{i}_unsupported_country:{cc}"

    pam = body.get("promotionApplyMode", "highest_threshold_wins")
    if pam != "highest_threshold_wins":
        return "invalid_promotion_apply_mode"

    return None


def normalize_rules_dict(body: dict[str, Any]) -> dict[str, Any]:
    """Produce canonical dict after validate_rules passed."""
    mk = body["markup"]
    dfl = mk["default"]
    byc_raw = mk.get("byCountry") or {}
    byc: dict[str, Any] = {}
    for k, row in sorted(byc_raw.items(), key=lambda x: str(x[0]).upper()):
        cc = str(k).strip().upper()
        byc[cc] = {
            "addPercent": float(row["addPercent"]),
            "addFixed": float(row["addFixed"]),
        }
    promos_raw = body.get("promotions") or []
    promos: list[dict[str, Any]] = []
    for p in promos_raw:
        cty = p.get("country")
        if cty is None or cty == "":
            country_out: str | None = None
        else:
            country_out = str(cty).strip().upper()
        promos.append(
            {
                "id": str(p["id"]).strip(),
                "minCartSubtotal": float(p["minCartSubtotal"]),
                "discountType": str(p["discountType"]),
                "discountValue": float(p["discountValue"]),
                "country": country_out,
            }
        )
    promos.sort(key=lambda x: (x["minCartSubtotal"], x["id"]))

    return {
        "version": int(body.get("version", 1)),
        "markup": {
            "default": {
                "addPercent": float(dfl["addPercent"]),
                "addFixed": float(dfl["addFixed"]),
            },
            "byCountry": byc,
        },
        "promotions": promos,
        "promotionApplyMode": "highest_threshold_wins",
    }


def normalize_for_storage(body: dict[str, Any]) -> str:
    norm = normalize_rules_dict(body)
    return json.dumps(norm, ensure_ascii=False, sort_keys=True)


def parse_rules_from_meta(table, meta: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Load from Dynamo METADATA item; invalid stored JSON or schema → defaults + warning code."""
    raw = meta.get(MERCHANT_PREMIUM_RULES_JSON)
    if raw is None or raw == "":
        return default_rules(), None
    if not isinstance(raw, str):
        return default_rules(), "invalid_stored_type"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default_rules(), "invalid_json"
    if not isinstance(data, dict):
        return default_rules(), "not_object"
    err = validate_rules(table, data)
    if err:
        return default_rules(), f"schema_invalid:{err}"
    return normalize_rules_dict(data), None

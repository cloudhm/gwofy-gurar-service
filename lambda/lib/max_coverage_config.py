"""Global + shop max coverage amounts keyed by ISO currency only (not per country)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .models import PK_GLOBAL_CONFIG, SK_MAX_COVERAGE_BY_CURRENCY
from .pricing_currencies import ALLOWED_PRICING_CURRENCIES, normalize_currency_code


def _parse_amounts_map(raw: Any, *, label: str) -> tuple[dict[str, float] | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, f"{label} must be an object"
    out: dict[str, float] = {}
    if len(raw) > 40:
        return None, f"{label}: too many currency entries"
    for ck, amt in raw.items():
        cy = normalize_currency_code(str(ck))
        if len(cy) != 3 or not cy.isalpha():
            return None, f"{label}: invalid currency code {ck!r}"
        if cy not in ALLOWED_PRICING_CURRENCIES:
            return None, f"{label}: currency not allowed: {cy}"
        try:
            x = float(amt)
        except (TypeError, ValueError):
            return None, f"{label}: invalid amount for {cy}"
        if x <= 0:
            return None, f"{label}: amount must be positive for {cy}"
        out[cy] = x
    return (out if out else None), None


def get_global_max_coverage_by_currency(table) -> dict[str, float]:
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_MAX_COVERAGE_BY_CURRENCY}).get("Item")
    raw = item.get("amounts_json") if item else None
    if not isinstance(raw, str):
        return {"USD": 9000.0}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"USD": 9000.0}
    if not isinstance(data, dict):
        return {"USD": 9000.0}
    out: dict[str, float] = {}
    for k, v in data.items():
        cy = normalize_currency_code(str(k))
        if cy in ALLOWED_PRICING_CURRENCIES:
            try:
                x = float(v)
                if x > 0:
                    out[cy] = x
            except (TypeError, ValueError):
                continue
    return out if out else {"USD": 9000.0}


def put_global_max_coverage_by_currency(table, amounts: dict[str, Any], updated_by: str) -> None:
    m, err = _parse_amounts_map(amounts, label="amounts")
    if err:
        raise ValueError(err)
    if not m:
        raise ValueError("amounts must be non-empty")
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_CONFIG,
            "sk": SK_MAX_COVERAGE_BY_CURRENCY,
            "amounts_json": json.dumps(m, ensure_ascii=False, sort_keys=True),
            "updated_at": now,
            "updated_by": str(updated_by)[:500],
        }
    )


def ensure_max_coverage_seed(table_name: str) -> None:
    ddb = boto3.resource("dynamodb").Table(table_name)
    if ddb.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_MAX_COVERAGE_BY_CURRENCY}).get("Item"):
        return
    put_global_max_coverage_by_currency(ddb, {"USD": 9000.0}, "system_seed")


def _shop_override_map(meta: dict[str, Any]) -> dict[str, float]:
    raw = meta.get("sp_max_coverage_by_currency_json")
    if not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        cy = normalize_currency_code(str(k))
        if cy in ALLOWED_PRICING_CURRENCIES:
            try:
                x = float(v)
                if x > 0:
                    out[cy] = x
            except (TypeError, ValueError):
                continue
    return out


def merged_max_coverage_by_currency(table, meta: dict[str, Any]) -> dict[str, float]:
    base = dict(get_global_max_coverage_by_currency(table))
    base.update(_shop_override_map(meta))
    return base


def effective_max_coverage_for_cart(table, meta: dict[str, Any], shop_ccy: str | None) -> tuple[float, str]:
    """Resolve (amount, currency) for cart calcInfo using shop settlement currency only."""
    merged = merged_max_coverage_by_currency(table, meta)
    sc = normalize_currency_code(shop_ccy or "")
    if sc and sc in merged:
        return (float(merged[sc]), sc)
    if "USD" in merged:
        return (float(merged["USD"]), "USD")
    if meta.get("sp_max_coverage_usd") is not None:
        try:
            return (float(meta["sp_max_coverage_usd"]), "USD")
        except (TypeError, ValueError):
            pass
    return (9000.0, "USD")


def effective_max_coverage_usd(table, meta: dict[str, Any]) -> float:
    """USD component of merged map (for tier helpers); ignores country."""
    m = merged_max_coverage_by_currency(table, meta)
    if "USD" in m:
        return float(m["USD"])
    if meta.get("sp_max_coverage_usd") is not None:
        try:
            return float(meta["sp_max_coverage_usd"])
        except (TypeError, ValueError):
            pass
    return 9000.0


def validate_shop_max_coverage_by_currency(
    amounts: dict[str, Any],
    allowed_shop_currencies: frozenset[str] | None,
) -> str | None:
    """If allowed_shop_currencies is set, every key must be in the set (and allowlist already enforced by _parse)."""
    m, err = _parse_amounts_map(amounts, label="sp_max_coverage_by_currency")
    if err:
        return err
    if not m:
        return "sp_max_coverage_by_currency must be non-empty"
    if allowed_shop_currencies is not None and len(allowed_shop_currencies) == 0:
        return "shop_enabled_currencies_not_synced"
    if allowed_shop_currencies is not None:
        for cy in m:
            if cy not in allowed_shop_currencies:
                return f"currency_not_enabled_for_shop: {cy}"
    return None


def normalize_shop_max_coverage_for_storage(amounts: dict[str, Any]) -> dict[str, float]:
    m, err = _parse_amounts_map(amounts, label="sp_max_coverage_by_currency")
    if err:
        raise ValueError(err)
    if not m:
        raise ValueError("empty amounts")
    return m

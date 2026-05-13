"""Global supported markets: default SP rate per ISO country (max coverage is currency-only; see max_coverage_config)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .models import PK_GLOBAL_CONFIG, SK_SHIPPING_COUNTRY_DEFAULTS


def _parse_rates_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, str):
        return {}
    try:
        m = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(m, dict):
        return {}
    return {str(k).upper(): str(v).strip() for k, v in m.items()}


def default_countries_bootstrap() -> dict[str, dict[str, Any]]:
    """Seed until admin replaces (ISO2 → rate only)."""
    rate = "0.04"
    codes = ("US", "CA", "GB", "AU", "DE", "FR", "IT", "ES", "NL", "JP", "NZ", "IE", "AT", "BE", "SE", "PL")
    return {c: {"rate": rate} for c in codes}


def validate_countries_payload(countries: Any) -> str | None:
    if not isinstance(countries, dict):
        return "countries must be an object keyed by ISO country code"
    if len(countries) > 400:
        return "too many country entries"
    if len(countries) == 0:
        return None
    for k, v in countries.items():
        cc = str(k).strip().upper()
        if len(cc) < 2 or len(cc) > 3:
            return f"invalid country code: {k!r}"
        if not isinstance(v, dict):
            return f"{cc}: value must be an object"
        if "rate" not in v:
            return f"{cc}: requires rate"
        try:
            float(str(v["rate"]).strip())
        except (TypeError, ValueError):
            return f"{cc}: invalid rate"
    return None


def get_shipping_country_defaults(table) -> dict[str, dict[str, Any]]:
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_SHIPPING_COUNTRY_DEFAULTS}).get("Item")
    if not item:
        return default_countries_bootstrap()
    raw = item.get("countries_json")
    if not isinstance(raw, str):
        return default_countries_bootstrap()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default_countries_bootstrap()
    if not isinstance(data, dict):
        return default_countries_bootstrap()
    out: dict[str, dict[str, Any]] = {}
    for k, v in data.items():
        cc = str(k).strip().upper()
        if isinstance(v, dict):
            out[cc] = dict(v)
    return out


def put_shipping_country_defaults(table, countries: dict[str, dict[str, Any]], updated_by: str) -> None:
    err = validate_countries_payload(countries)
    if err:
        raise ValueError(err)
    norm: dict[str, dict[str, Any]] = {}
    for k, v in countries.items():
        cc = str(k).strip().upper()
        norm[cc] = {"rate": str(v["rate"]).strip()}
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_CONFIG,
            "sk": SK_SHIPPING_COUNTRY_DEFAULTS,
            "countries_json": json.dumps(norm, ensure_ascii=False, sort_keys=True),
            "updated_at": now,
            "updated_by": str(updated_by)[:500],
        }
    )


def ensure_shipping_country_defaults_seed(table_name: str) -> None:
    ddb = boto3.resource("dynamodb").Table(table_name)
    existing = ddb.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_SHIPPING_COUNTRY_DEFAULTS}).get("Item")
    if existing:
        return
    put_shipping_country_defaults(ddb, default_countries_bootstrap(), "system_seed")


def is_country_supported(table, country_cc: str) -> bool:
    cc = (country_cc or "").strip().upper()
    if not cc:
        return False
    return cc in get_shipping_country_defaults(table)


def effective_rate(table, meta: dict[str, Any], country_cc: str) -> str:
    """Shop `sp_market_rates_json` overrides global default rate for this country."""
    cc = (country_cc or "").strip().upper()
    shop_rates = _parse_rates_map(meta.get("sp_market_rates_json"))
    if cc in shop_rates and shop_rates[cc] != "":
        return shop_rates[cc]
    g = get_shipping_country_defaults(table)
    row = g.get(cc) or {}
    return str(row.get("rate", "0.04")).strip() or "0.04"


def supported_country_codes(table) -> set[str]:
    return set(get_shipping_country_defaults(table).keys())

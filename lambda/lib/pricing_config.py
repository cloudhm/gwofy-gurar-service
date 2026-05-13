"""Per-currency pricing tiers in DynamoDB (GLOBAL#CONFIG)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .default_protection_tiers import build_default_tiers
from .models import PK_GLOBAL_CONFIG, SK_PRICING_MODEL_DEFAULT, SK_SUPPORTED_CURRENCIES, sk_pricing_model
from .pricing_currencies import (
    ALLOWED_PRICING_CURRENCIES,
    normalize_currency_code,
    validate_supported_currencies_list,
)


def default_tiers_json() -> str:
    """98 default variants (plan_code + native `price` for template currency)."""
    return json.dumps(build_default_tiers(), ensure_ascii=False)


def tier_identity_from_row(t: dict[str, Any]) -> str:
    """Single tier id: plan_code, else legacy sku."""
    return str(t.get("plan_code") or t.get("sku") or "").strip()


def tier_native_amount(t: dict[str, Any]) -> float | None:
    """Read per-tier native price from `price` or legacy `price_usd`."""
    if "price" in t and t["price"] is not None:
        try:
            return float(str(t["price"]).strip())
        except (TypeError, ValueError):
            return None
    if "price_usd" in t and t["price_usd"] is not None:
        try:
            return float(str(t["price_usd"]).strip())
        except (TypeError, ValueError):
            return None
    return None


def stored_tier_codes(table, currency: str) -> set[str]:
    """Plan codes already persisted for this currency (forbid removals on PUT)."""
    ccy = normalize_currency_code(currency)
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": sk_pricing_model(ccy)}).get("Item")
    raw = item.get("tiers_json") if item else None
    if not isinstance(raw, str):
        return set()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, list):
        return set()
    out: set[str] = set()
    for t in data:
        if isinstance(t, dict):
            ident = tier_identity_from_row(t)
            if ident:
                out.add(ident)
    return out


def validate_tiers(tiers: Any) -> str | None:
    if not isinstance(tiers, list):
        return "tiers must be a JSON array"
    if len(tiers) < 1 or len(tiers) > 200:
        return "tiers length must be 1..200"
    seen: set[str] = set()
    for i, t in enumerate(tiers):
        if not isinstance(t, dict):
            return f"tier[{i}] must be an object"
        pc = str(t.get("plan_code") or "").strip()
        sk = str(t.get("sku") or "").strip()
        if pc and sk and pc != sk:
            return f"tier[{i}] plan_code and sku must match when both are set"
        ident = pc or sk
        if not ident:
            return f"tier[{i}] requires plan_code (or sku as alias)"
        if tier_native_amount(t) is None:
            return f"tier[{i}] missing or invalid price (or legacy price_usd)"
        if ident in seen:
            return f"duplicate tier plan_code: {ident}"
        seen.add(ident)
    return None


def normalize_tiers_for_storage(tiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist plan_code + native `price` (plan_code doubles as Shopify variant SKU)."""
    out: list[dict[str, Any]] = []
    for t in tiers:
        pc = str(t.get("plan_code") or "").strip()
        sk = str(t.get("sku") or "").strip()
        ident = pc or sk
        amt = tier_native_amount(t)
        assert amt is not None
        out.append({"plan_code": ident, "price": float(amt)})
    return out


def get_pricing_rows(table, currency: str) -> list[dict[str, Any]]:
    """Load tiers from Dynamo for `currency`; empty list if missing or invalid."""
    ccy = normalize_currency_code(currency)
    if not ccy:
        return []
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": sk_pricing_model(ccy)}).get("Item")
    if not item or not item.get("tiers_json"):
        return []
    try:
        data = json.loads(item["tiers_json"])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return data


def get_pricing_model(table, currency: str) -> list[dict[str, Any]]:
    """Tiers for admin/display: persisted rows, else USD in-code defaults only."""
    ccy = normalize_currency_code(currency)
    rows = get_pricing_rows(table, ccy)
    if rows:
        return rows
    if ccy == "USD":
        return build_default_tiers()
    return []


def put_pricing_model(
    table,
    tiers: list[dict[str, Any]],
    updated_by: str,
    *,
    currency: str,
) -> None:
    ccy = normalize_currency_code(currency)
    if len(ccy) != 3 or ccy not in ALLOWED_PRICING_CURRENCIES:
        raise ValueError(f"invalid or disallowed currency: {currency!r}")
    err = validate_tiers(tiers)
    if err:
        raise ValueError(err)
    norm = normalize_tiers_for_storage(tiers)
    previous = stored_tier_codes(table, ccy)
    new_codes = {row["plan_code"] for row in norm}
    removed = previous - new_codes
    if removed:
        raise ValueError(
            "cannot_remove_existing_tier_codes; missing: " + ", ".join(sorted(removed))
        )
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_CONFIG,
            "sk": sk_pricing_model(ccy),
            "tiers_json": json.dumps(norm, ensure_ascii=False),
            "updated_at": now,
            "updated_by": updated_by[:500],
            "currency": ccy,
        }
    )


def get_supported_currencies(table) -> list[str]:
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_SUPPORTED_CURRENCIES}).get("Item")
    raw = item.get("currencies_json") if item else None
    if not isinstance(raw, str):
        return ["USD"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ["USD"]
    if not isinstance(data, list):
        return ["USD"]
    out = [normalize_currency_code(str(x)) for x in data if str(x).strip()]
    return out if out else ["USD"]


def put_supported_currencies(table, currencies: list[str], updated_by: str) -> None:
    err = validate_supported_currencies_list(currencies)
    if err:
        raise ValueError(err)
    norm = [normalize_currency_code(str(c)) for c in currencies]
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_CONFIG,
            "sk": SK_SUPPORTED_CURRENCIES,
            "currencies_json": json.dumps(norm, ensure_ascii=False),
            "updated_at": now,
            "updated_by": updated_by[:500],
        }
    )


def migrate_legacy_pricing_if_needed(table) -> None:
    """Copy PRICING_MODEL_DEFAULT -> PRICING_MODEL#USD; seed supported list."""
    usd_sk = sk_pricing_model("USD")
    usd_item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": usd_sk}).get("Item")
    legacy = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_PRICING_MODEL_DEFAULT}).get("Item")

    if not usd_item and legacy and legacy.get("tiers_json"):
        try:
            tiers = json.loads(legacy["tiers_json"])
        except json.JSONDecodeError:
            tiers = None
        if isinstance(tiers, list) and tiers:
            norm = []
            for t in tiers:
                if not isinstance(t, dict):
                    continue
                ident = tier_identity_from_row(t)
                if not ident:
                    continue
                amt = tier_native_amount(t)
                if amt is None:
                    continue
                norm.append({"plan_code": ident, "price": float(amt)})
            if norm:
                now = datetime.now(timezone.utc).isoformat()
                table.put_item(
                    Item={
                        "pk": PK_GLOBAL_CONFIG,
                        "sk": usd_sk,
                        "tiers_json": json.dumps(norm, ensure_ascii=False),
                        "updated_at": now,
                        "updated_by": "migrate_legacy_pricing",
                        "currency": "USD",
                    }
                )

    sup_item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_SUPPORTED_CURRENCIES}).get("Item")
    if not sup_item:
        now = datetime.now(timezone.utc).isoformat()
        table.put_item(
            Item={
                "pk": PK_GLOBAL_CONFIG,
                "sk": SK_SUPPORTED_CURRENCIES,
                "currencies_json": json.dumps(["USD"], ensure_ascii=False),
                "updated_at": now,
                "updated_by": "migrate_legacy_pricing",
            }
        )


def ensure_default_pricing_seed(table_name: str) -> None:
    """Migrate legacy global row, then ensure USD tiers + supported currencies exist."""
    ddb = boto3.resource("dynamodb").Table(table_name)
    migrate_legacy_pricing_if_needed(ddb)
    usd_sk = sk_pricing_model("USD")
    if not ddb.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": usd_sk}).get("Item"):
        put_pricing_model(ddb, build_default_tiers(), "system_seed", currency="USD")
    if not ddb.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_SUPPORTED_CURRENCIES}).get("Item"):
        put_supported_currencies(ddb, ["USD"], "system_seed")

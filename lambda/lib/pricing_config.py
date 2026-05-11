"""Global default pricing model (USD bands) in DynamoDB."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .default_protection_tiers import build_default_tiers
from .models import PK_GLOBAL_CONFIG, SK_PRICING_MODEL_DEFAULT


def default_tiers_json() -> str:
    """98 default variants (plan_code S0001–S0098 + price_usd)."""
    return json.dumps(build_default_tiers(), ensure_ascii=False)


def tier_identity_from_row(t: dict[str, Any]) -> str:
    """Single tier id: plan_code, else legacy sku."""
    return str(t.get("plan_code") or t.get("sku") or "").strip()


def stored_tier_codes(table) -> set[str]:
    """Plan codes / SKUs already persisted (not defaults). Used to forbid removals on PUT."""
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_PRICING_MODEL_DEFAULT}).get("Item")
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
        if "price_usd" not in t:
            return f"tier[{i}] missing price_usd"
        try:
            float(str(t["price_usd"]).strip())
        except (TypeError, ValueError):
            return f"tier[{i}] invalid price_usd"
        if ident in seen:
            return f"duplicate tier plan_code: {ident}"
        seen.add(ident)
    return None


def normalize_tiers_for_storage(tiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist only plan_code + price_usd (plan_code doubles as Shopify variant SKU)."""
    out: list[dict[str, Any]] = []
    for t in tiers:
        pc = str(t.get("plan_code") or "").strip()
        sk = str(t.get("sku") or "").strip()
        ident = pc or sk
        out.append(
            {
                "plan_code": ident,
                "price_usd": float(str(t["price_usd"]).strip()),
            }
        )
    return out


def get_pricing_model(table) -> list[dict[str, Any]]:
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_PRICING_MODEL_DEFAULT}).get("Item")
    if not item or not item.get("tiers_json"):
        return json.loads(default_tiers_json())
    try:
        return json.loads(item["tiers_json"])
    except json.JSONDecodeError:
        return json.loads(default_tiers_json())


def put_pricing_model(table, tiers: list[dict[str, Any]], updated_by: str) -> None:
    err = validate_tiers(tiers)
    if err:
        raise ValueError(err)
    norm = normalize_tiers_for_storage(tiers)
    previous = stored_tier_codes(table)
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
            "sk": SK_PRICING_MODEL_DEFAULT,
            "tiers_json": json.dumps(norm, ensure_ascii=False),
            "updated_at": now,
            "updated_by": updated_by[:500],
        }
    )


def ensure_default_pricing_seed(table_name: str) -> None:
    """Idempotent seed if row missing (e.g. first deploy)."""
    ddb = boto3.resource("dynamodb").Table(table_name)
    existing = ddb.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_PRICING_MODEL_DEFAULT}).get("Item")
    if existing:
        return
    put_pricing_model(ddb, json.loads(default_tiers_json()), "system_seed")

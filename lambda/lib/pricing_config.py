"""Global default pricing model (USD bands) in DynamoDB."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .default_protection_tiers import build_default_tiers
from .models import PK_GLOBAL_CONFIG, SK_PRICING_MODEL_DEFAULT


def default_tiers_json() -> str:
    """98 default variants (plan_code / sku S0001–S0098, USD bands, addon price_usd)."""
    return json.dumps(build_default_tiers(), ensure_ascii=False)


def validate_tiers(tiers: Any) -> str | None:
    if not isinstance(tiers, list):
        return "tiers must be a JSON array"
    if len(tiers) < 1 or len(tiers) > 200:
        return "tiers length must be 1..200"
    for i, t in enumerate(tiers):
        if not isinstance(t, dict):
            return f"tier[{i}] must be an object"
        for k in ("plan_code", "min_usd", "max_usd", "price_usd"):
            if k not in t:
                return f"tier[{i}] missing {k}"
    return None


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
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_CONFIG,
            "sk": SK_PRICING_MODEL_DEFAULT,
            "tiers_json": json.dumps(tiers, ensure_ascii=False),
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

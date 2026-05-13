"""Global + per-shop cart calc coverage tip strings (calcInfo)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .models import (
    META_SP_BELOW_MIN_COVERAGE_TIP,
    META_SP_GREATER_MAX_COVERAGE_TIP,
    PK_GLOBAL_CONFIG,
    SK_CALC_COVERAGE_TIPS,
)

MAX_TIP_LEN = 5000


def default_calc_coverage_tips_global() -> dict[str, str]:
    return {"spBelowMinCoverageTip": "", "spGreaterMaxCoverageTip": ""}


def validate_calc_coverage_tips_global(body: Any) -> dict[str, str]:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    if "spBelowMinCoverageTip" not in body or "spGreaterMaxCoverageTip" not in body:
        raise ValueError("spBelowMinCoverageTip and spGreaterMaxCoverageTip are required")
    a = body["spBelowMinCoverageTip"]
    b = body["spGreaterMaxCoverageTip"]
    if not isinstance(a, str) or not isinstance(b, str):
        raise ValueError("tips must be strings")
    if len(a) > MAX_TIP_LEN or len(b) > MAX_TIP_LEN:
        raise ValueError(f"tips exceed {MAX_TIP_LEN} characters")
    return {"spBelowMinCoverageTip": a, "spGreaterMaxCoverageTip": b}


def get_calc_coverage_tips_global(table) -> dict[str, str]:
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_CALC_COVERAGE_TIPS}).get("Item")
    raw = item.get("calc_coverage_tips_json") if item else None
    if not isinstance(raw, str):
        return default_calc_coverage_tips_global()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default_calc_coverage_tips_global()
    if not isinstance(data, dict):
        return default_calc_coverage_tips_global()
    base = default_calc_coverage_tips_global()
    for k in base:
        if k in data and isinstance(data[k], str):
            base[k] = data[k][:MAX_TIP_LEN]
    return base


def put_calc_coverage_tips_global(table, payload: dict[str, Any], updated_by: str) -> None:
    norm = validate_calc_coverage_tips_global(payload)
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_CONFIG,
            "sk": SK_CALC_COVERAGE_TIPS,
            "calc_coverage_tips_json": json.dumps(norm, ensure_ascii=False),
            "updated_at": now,
            "updated_by": str(updated_by)[:500],
        }
    )


def effective_calc_coverage_tips(table, meta: dict[str, Any]) -> dict[str, str]:
    """Merge global defaults with optional shop METADATA overrides."""
    g = get_calc_coverage_tips_global(table)
    below = g["spBelowMinCoverageTip"]
    greater = g["spGreaterMaxCoverageTip"]
    if META_SP_BELOW_MIN_COVERAGE_TIP in meta and meta[META_SP_BELOW_MIN_COVERAGE_TIP] is not None:
        below = str(meta[META_SP_BELOW_MIN_COVERAGE_TIP])[:MAX_TIP_LEN]
    if META_SP_GREATER_MAX_COVERAGE_TIP in meta and meta[META_SP_GREATER_MAX_COVERAGE_TIP] is not None:
        greater = str(meta[META_SP_GREATER_MAX_COVERAGE_TIP])[:MAX_TIP_LEN]
    return {"spBelowMinCoverageTip": below, "spGreaterMaxCoverageTip": greater}


def shop_override_snapshot(meta: dict[str, Any]) -> dict[str, str | None]:
    """Values stored on shop METADATA, or None when inheriting global."""
    out: dict[str, str | None] = {
        "spBelowMinCoverageTip": None,
        "spGreaterMaxCoverageTip": None,
    }
    if META_SP_BELOW_MIN_COVERAGE_TIP in meta:
        out["spBelowMinCoverageTip"] = str(meta[META_SP_BELOW_MIN_COVERAGE_TIP])
    if META_SP_GREATER_MAX_COVERAGE_TIP in meta:
        out["spGreaterMaxCoverageTip"] = str(meta[META_SP_GREATER_MAX_COVERAGE_TIP])
    return out


def validate_shop_tip_value(v: Any) -> str | None:
    """None means remove override; str stores override."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("tip must be a string or null")
    if len(v) > MAX_TIP_LEN:
        raise ValueError(f"tip exceeds {MAX_TIP_LEN} characters")
    return v


def ensure_calc_coverage_tips_seed(table_name: str) -> None:
    ddb = boto3.resource("dynamodb").Table(table_name)
    existing = ddb.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_CALC_COVERAGE_TIPS}).get("Item")
    if existing:
        return
    put_calc_coverage_tips_global(ddb, default_calc_coverage_tips_global(), "system_seed")

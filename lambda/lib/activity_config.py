"""Global cart `activityInfo` (GLOBAL#CONFIG / ACTIVITY_INFO)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .models import PK_GLOBAL_CONFIG, SK_ACTIVITY_INFO


def default_activity_dict() -> dict[str, Any]:
    return {"activityExtInfo": "", "activityState": 0}


def validate_activity_payload(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    if "activityExtInfo" not in body or "activityState" not in body:
        raise ValueError("activityExtInfo and activityState are required")
    ext = body["activityExtInfo"]
    st = body["activityState"]
    if not isinstance(ext, str):
        raise ValueError("activityExtInfo must be a string")
    if len(ext) > 10000:
        raise ValueError("activityExtInfo exceeds 10000 characters")
    if not isinstance(st, int):
        raise ValueError("activityState must be an integer")
    return {"activityExtInfo": ext, "activityState": st}


def get_activity_info(table) -> dict[str, Any]:
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_ACTIVITY_INFO}).get("Item")
    raw = item.get("activity_info_json") if item else None
    if not isinstance(raw, str):
        return default_activity_dict()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default_activity_dict()
    if not isinstance(data, dict):
        return default_activity_dict()
    ext = data.get("activityExtInfo", "")
    st = data.get("activityState", 0)
    if not isinstance(ext, str):
        ext = ""
    if not isinstance(st, int):
        try:
            st = int(st)
        except (TypeError, ValueError):
            st = 0
    return {"activityExtInfo": ext, "activityState": st}


def put_activity_info(table, payload: dict[str, Any], updated_by: str) -> None:
    norm = validate_activity_payload(payload)
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_CONFIG,
            "sk": SK_ACTIVITY_INFO,
            "activity_info_json": json.dumps(norm, ensure_ascii=False),
            "updated_at": now,
            "updated_by": str(updated_by)[:500],
        }
    )


def ensure_activity_info_seed(table_name: str) -> None:
    ddb = boto3.resource("dynamodb").Table(table_name)
    existing = ddb.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_ACTIVITY_INFO}).get("Item")
    if existing:
        return
    put_activity_info(ddb, default_activity_dict(), "system_seed")

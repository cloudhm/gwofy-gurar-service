"""Global cart `tipsInfo` (GLOBAL#CONFIG / TIPS_INFO)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3

from .models import PK_GLOBAL_CONFIG, SK_TIPS_INFO


def default_tips_dict() -> dict[str, Any]:
    return {
        "ppVersion": {
            "faqUrl": "https://gwofy.com/docs/pp-faq",
            "locationType": "normal",
            "popup": "gwofy",
            "terms": "https://gwofy.com/terms/pp",
        },
        "spVersion": {
            "faqUrl": "https://gwofy.com/docs/sp-faq",
            "popup": "SP-GWOFY",
            "terms": "https://gwofy.com/terms/sp",
        },
    }


def _nonempty_str(v: Any, field: str) -> str:
    if not isinstance(v, str):
        raise ValueError(f"{field} must be a string")
    if len(v) > 2048:
        raise ValueError(f"{field} exceeds 2048 characters")
    return v


def validate_tips_payload(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    if "ppVersion" not in body or "spVersion" not in body:
        raise ValueError("ppVersion and spVersion are required")
    pp = body["ppVersion"]
    sp = body["spVersion"]
    if not isinstance(pp, dict):
        raise ValueError("ppVersion must be an object")
    if not isinstance(sp, dict):
        raise ValueError("spVersion must be an object")
    for k in ("faqUrl", "locationType", "popup", "terms"):
        if k not in pp:
            raise ValueError(f"ppVersion.{k} is required")
        _nonempty_str(pp[k], f"ppVersion.{k}")
    for k in ("faqUrl", "popup", "terms"):
        if k not in sp:
            raise ValueError(f"spVersion.{k} is required")
        _nonempty_str(sp[k], f"spVersion.{k}")
    return {
        "ppVersion": {
            "faqUrl": pp["faqUrl"],
            "locationType": pp["locationType"],
            "popup": pp["popup"],
            "terms": pp["terms"],
        },
        "spVersion": {
            "faqUrl": sp["faqUrl"],
            "popup": sp["popup"],
            "terms": sp["terms"],
        },
    }


def _merge_defaults(stored: dict[str, Any]) -> dict[str, Any]:
    base = default_tips_dict()
    out_pp = dict(base["ppVersion"])
    out_sp = dict(base["spVersion"])
    pp = stored.get("ppVersion")
    sp = stored.get("spVersion")
    if isinstance(pp, dict):
        for k in out_pp:
            if k in pp and isinstance(pp[k], str):
                out_pp[k] = pp[k][:2048]
    if isinstance(sp, dict):
        for k in out_sp:
            if k in sp and isinstance(sp[k], str):
                out_sp[k] = sp[k][:2048]
    return {"ppVersion": out_pp, "spVersion": out_sp}


def get_tips_info(table) -> dict[str, Any]:
    item = table.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_TIPS_INFO}).get("Item")
    raw = item.get("tips_info_json") if item else None
    if not isinstance(raw, str):
        return default_tips_dict()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default_tips_dict()
    if not isinstance(data, dict):
        return default_tips_dict()
    return _merge_defaults(data)


def put_tips_info(table, payload: dict[str, Any], updated_by: str) -> None:
    norm = validate_tips_payload(payload)
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_CONFIG,
            "sk": SK_TIPS_INFO,
            "tips_info_json": json.dumps(norm, ensure_ascii=False),
            "updated_at": now,
            "updated_by": str(updated_by)[:500],
        }
    )


def ensure_tips_info_seed(table_name: str) -> None:
    ddb = boto3.resource("dynamodb").Table(table_name)
    existing = ddb.get_item(Key={"pk": PK_GLOBAL_CONFIG, "sk": SK_TIPS_INFO}).get("Item")
    if existing:
        return
    put_tips_info(ddb, default_tips_dict(), "system_seed")

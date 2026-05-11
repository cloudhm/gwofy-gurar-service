"""Append-only audit rows: pk=SHOP#shop, sk=AUDIT#<sortable>#<suffix>."""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from .models import SK_AUDIT_PREFIX, pk_shop


def _audit_sort_key() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    suf = secrets.token_hex(4)
    return f"{SK_AUDIT_PREFIX}{ts}#{suf}"


def append_audit(
    table,
    shop: str,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    outcome: str,
    resource: str | None = None,
    detail: dict[str, Any] | None = None,
    http_path: str | None = None,
    request_id: str | None = None,
    source_ip: str | None = None,
    actor_email: str | None = None,
) -> None:
    sk = _audit_sort_key()
    item: dict[str, Any] = {
        "pk": pk_shop(shop),
        "sk": sk,
        "actor_type": actor_type,
        "actor_id": (actor_id or "")[:500],
        "action": action[:200],
        "outcome": outcome[:50],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ttl": int(time.time()) + 400 * 24 * 3600,
    }
    if resource:
        item["resource"] = resource[:500]
    if detail:
        item["detail_json"] = json.dumps(detail, ensure_ascii=False, default=str)[:35000]
    if http_path:
        item["http_path"] = http_path[:500]
    if request_id:
        item["request_id"] = request_id[:200]
    if source_ip:
        item["source_ip"] = source_ip[:100]
    if actor_email:
        item["actor_email"] = actor_email[:320]
    table.put_item(Item=item)

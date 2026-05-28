"""Admin-uploaded global JS assets (DynamoDB GLOBAL#STATIC_JS / {filename})."""

from __future__ import annotations

import base64
import cgi
import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from .models import PK_GLOBAL_STATIC_JS

_RAW_JS_CONTENT_TYPES = frozenset(
    {
        "application/javascript",
        "application/x-javascript",
        "text/javascript",
        "text/plain",
    }
)

_MAX_SOURCE_BYTES = 350_000
_MAX_NAME_LEN = 128
_BLOCKED_NAMES = frozenset({"app-config.js"})
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*\.js$")


def validate_script_name(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError("name is required")
    n = name.strip()
    if not n or len(n) > _MAX_NAME_LEN:
        raise ValueError("invalid script name")
    if ".." in n or "/" in n or "\\" in n:
        raise ValueError("invalid script name")
    if n.lower() in _BLOCKED_NAMES:
        raise ValueError("reserved script name")
    if not _NAME_RE.match(n):
        raise ValueError("invalid script name")
    return n


def validate_source(source: Any) -> str:
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    if not source.strip():
        raise ValueError("source must not be empty")
    encoded = source.encode("utf-8")
    if len(encoded) > _MAX_SOURCE_BYTES:
        raise ValueError(f"source exceeds {_MAX_SOURCE_BYTES} bytes")
    return source


def _event_headers(event: dict[str, Any]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}


def _raw_body_bytes(event: dict[str, Any]) -> bytes:
    raw = event.get("body") or ""
    if isinstance(raw, str):
        text = raw
    else:
        text = str(raw)
    if event.get("isBase64Encoded"):
        return base64.b64decode(text)
    return text.encode("utf-8")


def _confirm_overwrite_from_event(event: dict[str, Any], body_flag: bool = False) -> bool:
    if body_flag:
        return True
    qs = event.get("queryStringParameters") or {}
    if isinstance(qs, dict):
        v = str(qs.get("confirmOverwrite") or "").strip().lower()
        if v in ("1", "true", "yes"):
            return True
    headers = _event_headers(event)
    hv = str(headers.get("x-confirm-overwrite") or "").strip().lower()
    return hv in ("1", "true", "yes")


def _parse_multipart_source(body_bytes: bytes, content_type: str) -> str:
    environ = {
        "REQUEST_METHOD": "PUT",
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(body_bytes)),
    }
    fs = cgi.FieldStorage(
        fp=io.BytesIO(body_bytes),
        environ=environ,
        headers={"content-type": content_type},
        keep_blank_values=True,
    )
    file_field = fs["file"] if "file" in fs else None
    if file_field is not None and getattr(file_field, "file", None) is not None:
        data = file_field.file.read()
        if isinstance(data, str):
            return data
        return data.decode("utf-8")
    if "source" in fs:
        val = fs["source"].value
        if isinstance(val, str):
            return val
        if val is not None:
            return str(val)
    raise ValueError("multipart body must include file or source field")


def parse_static_script_put_payload(event: dict[str, Any]) -> tuple[str, bool]:
    """
  Parse PUT body for static script upload.

  Supports:
  - application/json: { "source": "...", "confirmOverwrite": bool }
  - application/json: { "sourceBase64": "...", "confirmOverwrite": bool }
  - Raw JS Content-Type: body is the full script (confirm via query/header)
  - multipart/form-data: field `file` or `source` (confirm via query/header)
  """
    headers = _event_headers(event)
    ct_full = headers.get("content-type") or "application/json"
    ct = ct_full.split(";", 1)[0].strip().lower()
    body_bytes = _raw_body_bytes(event)

    if ct == "multipart/form-data":
        source = _parse_multipart_source(body_bytes, ct_full)
        confirm = _confirm_overwrite_from_event(event)
        return validate_source(source), confirm

    if ct in _RAW_JS_CONTENT_TYPES:
        source = body_bytes.decode("utf-8")
        confirm = _confirm_overwrite_from_event(event)
        return validate_source(source), confirm

    try:
        data = json.loads(body_bytes.decode("utf-8") if body_bytes else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("invalid_json") from e
    if not isinstance(data, dict):
        raise ValueError("body_must_be_object")

    confirm = _confirm_overwrite_from_event(event, bool(data.get("confirmOverwrite")))
    if "source" in data and data["source"] is not None:
        return validate_source(data["source"]), confirm
    b64 = data.get("sourceBase64")
    if isinstance(b64, str) and b64.strip():
        try:
            decoded = base64.b64decode(b64, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError("invalid_sourceBase64") from e
        return validate_source(decoded), confirm
    raise ValueError("source_or_sourceBase64_required")


def public_script_url(name: str) -> str | None:
    base = (os.environ.get("WEBHOOK_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/static/{quote(name, safe='')}"


def _etag_for_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()[:32]


def _item_to_summary(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("sk") or "")
    return {
        "name": name,
        "updatedAt": str(item.get("updated_at") or ""),
        "updatedBy": str(item.get("updated_by") or ""),
        "byteLength": int(item.get("byte_length") or 0),
        "publicUrl": public_script_url(name),
    }


def _item_to_detail(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("sk") or "")
    return {
        "name": name,
        "source": str(item.get("source") or ""),
        "updatedAt": str(item.get("updated_at") or ""),
        "updatedBy": str(item.get("updated_by") or ""),
        "byteLength": int(item.get("byte_length") or 0),
        "publicUrl": public_script_url(name),
        "exists": True,
    }


def list_scripts(table) -> list[dict[str, Any]]:
    from boto3.dynamodb.conditions import Key

    resp = table.query(KeyConditionExpression=Key("pk").eq(PK_GLOBAL_STATIC_JS))
    items = resp.get("Items") or []
    while resp.get("LastEvaluatedKey"):
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(PK_GLOBAL_STATIC_JS),
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items") or [])
    summaries = [_item_to_summary(it) for it in items]
    summaries.sort(key=lambda x: x["name"])
    return summaries


def get_script(table, name: str) -> dict[str, Any] | None:
    safe = validate_script_name(name)
    item = table.get_item(Key={"pk": PK_GLOBAL_STATIC_JS, "sk": safe}).get("Item")
    if not item:
        return None
    return _item_to_detail(item)


def script_exists(table, name: str) -> bool:
    safe = validate_script_name(name)
    item = table.get_item(Key={"pk": PK_GLOBAL_STATIC_JS, "sk": safe}).get("Item")
    return bool(item)


def put_script(
    table,
    name: str,
    source: str,
    *,
    updated_by: str,
    confirm_overwrite: bool,
) -> tuple[bool, dict[str, Any]]:
    """
    Returns (created, detail dict).
    Raises ValueError on validation errors.
    Raises FileExistsError when name exists and confirm_overwrite is False.
    """
    safe = validate_script_name(name)
    text = validate_source(source)
    body = text.encode("utf-8")
    exists = script_exists(table, safe)
    if exists and not confirm_overwrite:
        raise FileExistsError(safe)

    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": PK_GLOBAL_STATIC_JS,
            "sk": safe,
            "source": text,
            "byte_length": len(body),
            "content_sha256": _etag_for_body(body),
            "updated_at": now,
            "updated_by": str(updated_by)[:500],
        }
    )
    detail = get_script(table, safe)
    assert detail is not None
    return (not exists, detail)


def delete_script(table, name: str) -> bool:
    safe = validate_script_name(name)
    item = table.get_item(Key={"pk": PK_GLOBAL_STATIC_JS, "sk": safe}).get("Item")
    if not item:
        return False
    table.delete_item(Key={"pk": PK_GLOBAL_STATIC_JS, "sk": safe})
    return True


def resolve_public_script(name: str, table) -> tuple[bytes, str] | None:
    """Read-only: uploaded script body and etag, or None."""
    try:
        safe = validate_script_name(name)
    except ValueError:
        return None
    item = table.get_item(Key={"pk": PK_GLOBAL_STATIC_JS, "sk": safe}).get("Item")
    if not item:
        return None
    raw = item.get("source")
    if not isinstance(raw, str) or not raw:
        return None
    body = raw.encode("utf-8")
    stored_hash = str(item.get("content_sha256") or "")
    etag = stored_hash if stored_hash else _etag_for_body(body)
    return body, etag

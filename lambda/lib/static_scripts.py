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

from .models import APP_CONFIG_SCRIPT_NAME, GSI2_PK_SHOP_INDEX, PK_GLOBAL_STATIC_JS, SK_METADATA
from .gwofy_config_js_transform import validate_gwofy_config_assignment
from .storefront_gwofy_config import DEFAULT_APP_CONFIG_SCRIPT_NAME

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
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*\.js$")


class AppConfigScriptInUseError(Exception):
    def __init__(self, bound_shops: list[str]):
        self.bound_shops = bound_shops
        super().__init__(f"app_config_script_in_use:{','.join(bound_shops)}")


def script_name_rules() -> dict[str, Any]:
    """Machine-readable naming rules for Admin UI / API clients."""
    return {
        "pattern": r"^[a-zA-Z0-9][a-zA-Z0-9._-]*\.js$",
        "maxLength": _MAX_NAME_LEN,
        "mustEndWith": ".js",
        "firstChar": "ASCII letter or digit (a-z, A-Z, 0-9)",
        "allowedChars": "ASCII letters, digits, dot (.), underscore (_), hyphen (-) only",
        "disallowed": [
            "spaces and whitespace",
            "Chinese and other non-ASCII characters",
            "slashes and path segments (/, \\, ..)",
        ],
        "examplesValid": ["store1.js", "patch-v2.js", "app-config.js", "app.storefront.js"],
        "examplesInvalid": ["store 1.js", "店铺.js", "x/.js"],
    }


def validate_script_name(name: str) -> str:
    """
    Validate upload filename (URL path segment after /admin/static-scripts/).

    Raises ValueError with stable codes:
    - script_name_required
    - script_name_too_long
    - script_name_path_chars
    - script_name_whitespace
    - script_name_non_ascii
    - script_name_invalid_format
    """
    if not name or not isinstance(name, str):
        raise ValueError("script_name_required")
    n = name.strip()
    if not n:
        raise ValueError("script_name_required")
    if len(n) > _MAX_NAME_LEN:
        raise ValueError("script_name_too_long")
    if ".." in n or "/" in n or "\\" in n:
        raise ValueError("script_name_path_chars")
    if any(ch.isspace() for ch in n):
        raise ValueError("script_name_whitespace")
    if not n.isascii():
        raise ValueError("script_name_non_ascii")
    if not _NAME_RE.match(n):
        raise ValueError("script_name_invalid_format")
    return n


def validate_app_config_source(source: str) -> None:
    """Require g.GWOFY_CONFIG = ... assignment when isAppConfig is true on upload."""
    validate_gwofy_config_assignment(source)


def effective_app_config_script_name(meta: dict[str, Any] | None) -> str:
    if not meta:
        return DEFAULT_APP_CONFIG_SCRIPT_NAME
    stored = str(meta.get(APP_CONFIG_SCRIPT_NAME) or "").strip()
    return stored if stored else DEFAULT_APP_CONFIG_SCRIPT_NAME


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


def _truthy_query_param(raw: Any) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes"):
        return True
    if s in ("0", "false", "no"):
        return False
    return None


def _confirm_overwrite_from_event(event: dict[str, Any], body_flag: bool = False) -> bool:
    if body_flag:
        return True
    qs = event.get("queryStringParameters") or {}
    if isinstance(qs, dict):
        v = _truthy_query_param(qs.get("confirmOverwrite"))
        if v is True:
            return True
    headers = _event_headers(event)
    hv = str(headers.get("x-confirm-overwrite") or "").strip().lower()
    return hv in ("1", "true", "yes")


def _is_app_config_from_event(event: dict[str, Any], *, body_flag: bool | None = None) -> bool:
    """
    Resolve isAppConfig for PUT uploads.

    JSON body explicit isAppConfig wins when body_flag is not None.
    Otherwise query ?isAppConfig= or header X-Is-App-Config.
    """
    if body_flag is not None:
        return bool(body_flag)
    qs = event.get("queryStringParameters") or {}
    if isinstance(qs, dict):
        v = _truthy_query_param(qs.get("isAppConfig"))
        if v is not None:
            return v
    headers = _event_headers(event)
    hv = str(headers.get("x-is-app-config") or "").strip().lower()
    if hv in ("1", "true", "yes"):
        return True
    if hv in ("0", "false", "no"):
        return False
    return False


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


def parse_static_script_put_payload(event: dict[str, Any]) -> tuple[str, bool, bool]:
    """
    Parse PUT body for static script upload.

    Returns (source, confirm_overwrite, is_app_config).

    Supports:
    - application/json: { "source": "...", "confirmOverwrite": bool, "isAppConfig": bool }
    - application/json: { "sourceBase64": "...", ... }
    - Raw JS Content-Type: body is the full script; confirmOverwrite / isAppConfig via query or header
    - multipart/form-data: field `file` or `source`; confirmOverwrite / isAppConfig via query or header
    """
    headers = _event_headers(event)
    ct_full = headers.get("content-type") or "application/json"
    ct = ct_full.split(";", 1)[0].strip().lower()
    body_bytes = _raw_body_bytes(event)

    if ct == "multipart/form-data":
        source = _parse_multipart_source(body_bytes, ct_full)
        confirm = _confirm_overwrite_from_event(event)
        is_app_config = _is_app_config_from_event(event)
        return validate_source(source), confirm, is_app_config

    if ct in _RAW_JS_CONTENT_TYPES:
        source = body_bytes.decode("utf-8")
        confirm = _confirm_overwrite_from_event(event)
        is_app_config = _is_app_config_from_event(event)
        return validate_source(source), confirm, is_app_config

    try:
        data = json.loads(body_bytes.decode("utf-8") if body_bytes else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("invalid_json") from e
    if not isinstance(data, dict):
        raise ValueError("body_must_be_object")

    confirm = _confirm_overwrite_from_event(event, bool(data.get("confirmOverwrite")))
    body_is_app_config = bool(data["isAppConfig"]) if "isAppConfig" in data else None
    is_app_config = _is_app_config_from_event(event, body_flag=body_is_app_config)
    if "source" in data and data["source"] is not None:
        return validate_source(data["source"]), confirm, is_app_config
    b64 = data.get("sourceBase64")
    if isinstance(b64, str) and b64.strip():
        try:
            decoded = base64.b64decode(b64, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError("invalid_sourceBase64") from e
        return validate_source(decoded), confirm, is_app_config
    raise ValueError("source_or_sourceBase64_required")


def public_script_url(name: str) -> str | None:
    base = (os.environ.get("WEBHOOK_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/static/{quote(name, safe='')}"


def _etag_for_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()[:32]


def _item_is_app_config(item: dict[str, Any]) -> bool:
    return bool(item.get("is_app_config"))


def _item_to_summary(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("sk") or "")
    return {
        "name": name,
        "updatedAt": str(item.get("updated_at") or ""),
        "updatedBy": str(item.get("updated_by") or ""),
        "byteLength": int(item.get("byte_length") or 0),
        "publicUrl": public_script_url(name),
        "isAppConfig": _item_is_app_config(item),
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
        "contentSha256": str(item.get("content_sha256") or ""),
        "isAppConfig": _item_is_app_config(item),
        "exists": True,
    }


def parse_is_app_config_query(qs: dict[str, Any] | None) -> tuple[bool | None, str | None]:
    """
    Parse ?isAppConfig= query for GET /admin/static-scripts.

    Returns (filter, error):
    - (None, None) — no filter, return all scripts
    - (True, None) — only isAppConfig scripts
    - (False, None) — only non-app-config scripts
    """
    if not qs or not isinstance(qs, dict):
        return None, None
    v = _truthy_query_param(qs.get("isAppConfig"))
    if v is None and qs.get("isAppConfig") is not None:
        return None, "invalid_isAppConfig_query"
    return v, None


def list_scripts(table, *, is_app_config: bool | None = None) -> list[dict[str, Any]]:
    from boto3.dynamodb.conditions import Key

    resp = table.query(KeyConditionExpression=Key("pk").eq(PK_GLOBAL_STATIC_JS))
    items = resp.get("Items") or []
    while isinstance(resp.get("LastEvaluatedKey"), dict):
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(PK_GLOBAL_STATIC_JS),
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items") or [])
    summaries = [_item_to_summary(it) for it in items]
    if is_app_config is True:
        summaries = [s for s in summaries if s.get("isAppConfig")]
    elif is_app_config is False:
        summaries = [s for s in summaries if not s.get("isAppConfig")]
    summaries.sort(key=lambda x: x["name"])
    return summaries


def list_app_config_scripts(table) -> list[dict[str, Any]]:
    return list_scripts(table, is_app_config=True)


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


def shops_bound_to_script(table, script_name: str) -> list[str]:
    """Shops whose effective app-config template resolves to script_name."""
    safe = validate_script_name(script_name)
    from boto3.dynamodb.conditions import Key

    bound: list[str] = []
    kwargs: dict[str, Any] = {
        "IndexName": "GSI2",
        "KeyConditionExpression": Key("gsi2pk").eq(GSI2_PK_SHOP_INDEX),
    }
    resp = table.query(**kwargs)
    items = list(resp.get("Items") or [])
    while isinstance(resp.get("LastEvaluatedKey"), dict):
        resp = table.query(
            **kwargs,
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items") or [])
    for it in items:
        if it.get("sk") != SK_METADATA:
            continue
        if effective_app_config_script_name(it) != safe:
            continue
        shop = str(it.get("shop") or "").strip()
        if not shop and isinstance(it.get("pk"), str):
            pk = it["pk"]
            if pk.startswith("SHOP#"):
                shop = pk[5:]
        if shop:
            bound.append(shop)
    bound.sort()
    return bound


def put_script(
    table,
    name: str,
    source: str,
    *,
    updated_by: str,
    confirm_overwrite: bool,
    is_app_config: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """
    Returns (created, detail dict).
    Raises ValueError on validation errors.
    Raises FileExistsError when name exists and confirm_overwrite is False.
    """
    safe = validate_script_name(name)
    text = validate_source(source)
    if is_app_config:
        validate_app_config_source(text)
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
            "is_app_config": bool(is_app_config),
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
    bound = shops_bound_to_script(table, safe)
    if bound:
        raise AppConfigScriptInUseError(bound)
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

"""Pull Shopify Online Store themes + files into SHOP# Dynamo rows."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from .models import SK_METADATA, pk_shop
from .shopify_api import graphql_request

logger = logging.getLogger(__name__)

MAX_STORED_CONTENT_BYTES = 300 * 1024
THEMES_PAGE_SIZE = 50
FILES_PAGE_SIZE = 50

THEMES_PAGE_QUERY = """
query ThemesPage($cursor: String) {
  themes(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      role
      prefix
      processing
      processingFailed
      themeStoreId
      createdAt
      updatedAt
    }
  }
}
"""

THEME_FILES_PAGE_QUERY = """
query ThemeFilesPage($id: ID!, $cursor: String) {
  theme(id: $id) {
    id
    files(first: 50, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        filename
        body {
          ... on OnlineStoreThemeFileBodyText {
            content
          }
          ... on OnlineStoreThemeFileBodyBase64 {
            contentBase64
          }
        }
      }
    }
  }
}
"""


def _is_access_denied(errors: Any) -> bool:
    if not isinstance(errors, list):
        return False
    for err in errors:
        if not isinstance(err, dict):
            continue
        ext = err.get("extensions") or {}
        if ext.get("code") == "ACCESS_DENIED":
            return True
        msg = str(err.get("message") or "").lower()
        if "access denied" in msg or "access_denied" in msg:
            return True
    return False


def sk_theme_file(theme_gid: str, filename: str) -> str:
    """Build sort key for a theme file row (sk max 1024 bytes)."""
    safe_name = re.sub(r"[^\w.\-/]", "_", filename.strip())[:400]
    base = f"THEME#{theme_gid}#FILE#{safe_name}"
    if len(base.encode("utf-8")) > 1000:
        base = base.encode("utf-8")[:1000].decode("utf-8", errors="ignore")
    return base


def sk_theme(theme_gid: str) -> str:
    return f"THEME#{theme_gid}"


def _extract_file_body(node: dict[str, Any]) -> tuple[str, str | None, int]:
    """Return (body_kind, content_or_none, content_bytes)."""
    body = node.get("body") or {}
    if body.get("content") is not None:
        text = str(body.get("content") or "")
        return "text", text, len(text.encode("utf-8"))
    if body.get("contentBase64") is not None:
        b64 = str(body.get("contentBase64") or "")
        return "base64", b64, len(b64.encode("utf-8"))
    return "none", None, 0


def _theme_row_from_node(node: dict[str, Any], shop_norm: str, now: str) -> dict[str, Any]:
    gid = str(node.get("id") or "")
    return {
        "pk": pk_shop(shop_norm),
        "sk": sk_theme(gid),
        "shopify_id": gid,
        "name": str(node.get("name") or "")[:500],
        "role": str(node.get("role") or "")[:50],
        "prefix": str(node.get("prefix") or "")[:100],
        "processing": bool(node.get("processing")),
        "processing_failed": bool(node.get("processingFailed")),
        "theme_store_id": node.get("themeStoreId"),
        "created_at_source": str(node.get("createdAt") or "")[:40],
        "updated_at_source": str(node.get("updatedAt") or "")[:40],
        "synced_at": now,
    }


def _sync_theme_files(
    table,
    shop_norm: str,
    theme_gid: str,
    token: str,
    api_version: str,
    now: str,
) -> int:
    """Paginate theme files; return count of file rows written."""
    cursor: str | None = None
    written = 0
    while True:
        data = graphql_request(
            shop_norm,
            token,
            THEME_FILES_PAGE_QUERY,
            {"id": theme_gid, "cursor": cursor},
            api_version=api_version,
        )
        errors = data.get("errors")
        if errors:
            if _is_access_denied(errors):
                logger.warning(
                    "theme_files_sync_access_denied",
                    extra={"shop": shop_norm, "theme_gid": theme_gid},
                )
                return written
            raise RuntimeError(str(errors))
        theme_obj = (data.get("data") or {}).get("theme")
        if not theme_obj:
            break
        files_conn = theme_obj.get("files") or {}
        for node in files_conn.get("nodes") or []:
            filename = str(node.get("filename") or "").strip()
            if not filename:
                continue
            body_kind, content, content_bytes = _extract_file_body(node)
            item: dict[str, Any] = {
                "pk": pk_shop(shop_norm),
                "sk": sk_theme_file(theme_gid, filename),
                "shopify_theme_id": theme_gid,
                "filename": filename[:500],
                "body_kind": body_kind,
                "content_bytes": content_bytes,
                "content_oversized": False,
                "synced_at": now,
            }
            if content is not None and content_bytes <= MAX_STORED_CONTENT_BYTES:
                item["content"] = content
            elif content is not None:
                item["content_oversized"] = True
            table.put_item(Item=item)
            written += 1
        page = files_conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        if not cursor:
            break
    return written


def _fetch_all_themes(shop_norm: str, token: str, api_version: str) -> list[dict[str, Any]]:
    cursor: str | None = None
    themes: list[dict[str, Any]] = []
    while True:
        data = graphql_request(
            shop_norm,
            token,
            THEMES_PAGE_QUERY,
            {"cursor": cursor},
            api_version=api_version,
        )
        errors = data.get("errors")
        if errors:
            if _is_access_denied(errors):
                logger.warning("theme_sync_access_denied", extra={"shop": shop_norm})
                return []
            raise RuntimeError(str(errors))
        conn = (data.get("data") or {}).get("themes") or {}
        themes.extend(conn.get("nodes") or [])
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        if not cursor:
            break
    return themes


def fetch_main_theme_gid(shop_norm: str, token: str, api_version: str) -> str:
    """Return MAIN role theme GID from Shopify (no Dynamo writes)."""
    for node in _fetch_all_themes(shop_norm.strip().lower().rstrip("/"), token, api_version):
        if str(node.get("role") or "") == "MAIN":
            return str(node.get("id") or "")
    return ""


def update_main_theme_gid_metadata(
    table,
    shop_norm: str,
    main_gid: str,
    *,
    main_name: str = "",
) -> None:
    """Persist MAIN theme id on shop METADATA for embed deep links."""
    shop_norm = shop_norm.strip().lower().rstrip("/")
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression=(
            "SET main_theme_gid = :mg, main_theme_name = :mn, main_theme_role = :mr, updated_at = :u"
        ),
        ExpressionAttributeValues={
            ":mg": main_gid[:120],
            ":mn": (main_name or "")[:500],
            ":mr": "MAIN",
            ":u": now,
        },
    )


def sync_themes_full(
    table,
    shop: str,
    token: str,
    api_version: str,
) -> dict[str, Any]:
    """Fetch all themes + files; update SHOP METADATA summary. Returns sync stats."""
    shop_norm = shop.strip().lower().rstrip("/")
    now = datetime.now(timezone.utc).isoformat()
    themes = _fetch_all_themes(shop_norm, token, api_version)
    if not themes:
        return {"themes_count": 0, "files_count": 0, "skipped": True}

    main_gid = ""
    main_name = ""
    main_role = ""
    files_count = 0

    for node in themes:
        gid = str(node.get("id") or "")
        if not gid:
            continue
        table.put_item(Item=_theme_row_from_node(node, shop_norm, now))
        role = str(node.get("role") or "")
        if role == "MAIN":
            main_gid = gid
            main_name = str(node.get("name") or "")
            main_role = role
        try:
            files_count += _sync_theme_files(table, shop_norm, gid, token, api_version, now)
        except Exception:
            logger.warning(
                "theme_files_sync_failed",
                extra={"shop": shop_norm, "theme_gid": gid},
                exc_info=True,
            )

    expr_names: dict[str, str] = {
        "#u": "updated_at",
        "#ts": "themes_synced_at",
        "#tc": "themes_count",
        "#mg": "main_theme_gid",
        "#mn": "main_theme_name",
        "#mr": "main_theme_role",
    }
    expr_vals: dict[str, Any] = {
        ":u": now,
        ":ts": now,
        ":tc": len(themes),
        ":mg": main_gid[:120],
        ":mn": main_name[:500],
        ":mr": main_role[:50],
    }
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression=(
            "SET #u = :u, #ts = :ts, #tc = :tc, #mg = :mg, #mn = :mn, #mr = :mr"
        ),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_vals,
    )
    logger.info(
        "theme_sync_complete",
        extra={
            "shop": shop_norm,
            "themes_count": len(themes),
            "files_count": files_count,
            "main_theme_gid": main_gid,
        },
    )
    return {
        "themes_count": len(themes),
        "files_count": files_count,
        "main_theme_gid": main_gid,
        "skipped": False,
    }

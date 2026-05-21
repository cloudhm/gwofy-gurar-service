"""Theme sync — GraphQL pagination and Dynamo writes."""

import os
import sys

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda"))

from unittest.mock import MagicMock, patch

from lib.models import SK_METADATA, pk_shop
from lib.theme_sync import (
    MAX_STORED_CONTENT_BYTES,
    sk_theme,
    sk_theme_file,
    sync_themes_full,
)


def _themes_page(nodes, *, has_next=False, cursor=None):
    return {
        "data": {
            "themes": {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
            }
        }
    }


def _files_page(nodes, *, has_next=False, cursor=None):
    return {
        "data": {
            "theme": {
                "id": "gid://shopify/OnlineStoreTheme/1",
                "files": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                },
            }
        }
    }


def test_sync_themes_pagination_and_metadata():
    table = MagicMock()
    gid = "gid://shopify/OnlineStoreTheme/1"
    theme_main = {
        "id": gid,
        "name": "Dawn",
        "role": "MAIN",
        "prefix": "/t/1",
        "processing": False,
        "processingFailed": False,
        "themeStoreId": None,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-02T00:00:00Z",
    }
    theme_other = {
        "id": "gid://shopify/OnlineStoreTheme/2",
        "name": "Other",
        "role": "UNPUBLISHED",
        "prefix": "/t/2",
        "processing": False,
        "processingFailed": False,
        "themeStoreId": None,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }

    def fake_gql(shop, token, query, variables=None, api_version=None):
        if "ThemesPage" in query:
            if variables.get("cursor"):
                return _themes_page([theme_main])
            return _themes_page([theme_other], has_next=True, cursor="cur2")
        if "ThemeFilesPage" in query:
            tid = variables.get("id")
            if variables.get("cursor"):
                return _files_page([{"filename": f"{tid}/b.css", "body": {"content": "b {}"}}])
            return _files_page(
                [{"filename": f"{tid}/a.css", "body": {"content": "a {}"}}],
                has_next=True,
                cursor="fcur",
            )
        raise AssertionError(f"unexpected query: {query[:80]}")

    with patch("lib.theme_sync.graphql_request", side_effect=fake_gql):
        stats = sync_themes_full(table, "a.myshopify.com", "tok", "2026-04")

    assert stats["themes_count"] == 2
    assert stats["files_count"] == 4
    assert stats["main_theme_gid"] == gid
    theme_puts = [
        c.kwargs["Item"]
        for c in table.put_item.call_args_list
        if str(c.kwargs["Item"].get("sk", "")).startswith("THEME#")
        and "#FILE#" not in str(c.kwargs["Item"].get("sk", ""))
    ]
    assert len(theme_puts) == 2
    table.update_item.assert_called_once()
    upd = table.update_item.call_args.kwargs
    assert upd["Key"] == {"pk": pk_shop("a.myshopify.com"), "sk": SK_METADATA}
    vals = upd["ExpressionAttributeValues"]
    assert vals[":tc"] == 2
    assert vals[":mg"] == gid


def test_oversized_file_not_stored():
    table = MagicMock()
    gid = "gid://shopify/OnlineStoreTheme/9"
    huge = "x" * (MAX_STORED_CONTENT_BYTES + 1)
    theme_node = {
        "id": gid,
        "name": "Big",
        "role": "UNPUBLISHED",
        "prefix": "/t/9",
        "processing": False,
        "processingFailed": False,
        "themeStoreId": None,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
    }

    def fake_gql(shop, token, query, variables=None, api_version=None):
        if "ThemesPage" in query:
            return _themes_page([theme_node])
        if "ThemeFilesPage" in query:
            return _files_page([{"filename": "assets/huge.js", "body": {"content": huge}}])
        raise AssertionError("unexpected")

    with patch("lib.theme_sync.graphql_request", side_effect=fake_gql):
        sync_themes_full(table, "a.myshopify.com", "tok", "2026-04")

    file_puts = [
        c.kwargs["Item"]
        for c in table.put_item.call_args_list
        if str(c.kwargs["Item"].get("sk", "")).startswith("THEME#") and "#FILE#" in str(c.kwargs["Item"].get("sk", ""))
    ]
    assert len(file_puts) == 1
    assert file_puts[0]["content_oversized"] is True
    assert "content" not in file_puts[0]


def test_access_denied_skips_without_raise():
    table = MagicMock()

    def fake_gql(shop, token, query, variables=None, api_version=None):
        return {"errors": [{"message": "Access denied", "extensions": {"code": "ACCESS_DENIED"}}]}

    with patch("lib.theme_sync.graphql_request", side_effect=fake_gql):
        stats = sync_themes_full(table, "a.myshopify.com", "tok", "2026-04")

    assert stats["skipped"] is True
    assert stats["themes_count"] == 0
    table.update_item.assert_not_called()


def test_sk_helpers():
    gid = "gid://shopify/OnlineStoreTheme/1"
    assert sk_theme(gid) == f"THEME#{gid}"
    assert sk_theme_file(gid, "assets/foo.css").startswith(f"THEME#{gid}#FILE#")

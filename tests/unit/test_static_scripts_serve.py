"""GET /static/{name}.js — uploaded DynamoDB vs bundled fallback."""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("jwt", MagicMock())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "sec")
    monkeypatch.setenv("KMS_KEY_ID", "kms")


def _static_event(path: str, method: str = "GET", if_none_match: str | None = None):
    headers: dict[str, str] = {}
    if if_none_match is not None:
        headers["if-none-match"] = if_none_match
    return {
        "requestContext": {"http": {"method": method, "path": path}, "requestId": "r1"},
        "headers": headers,
    }


def test_serve_uploaded_script():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {
        "Item": {
            "pk": "GLOBAL#STATIC_JS",
            "sk": "custom.js",
            "source": "window.CUSTOM = true;",
            "content_sha256": "abc",
        }
    }

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_static_event("/static/custom.js"), None)

    assert out["statusCode"] == 200
    assert "window.CUSTOM = true;" in out["body"]


def test_unknown_script_404():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_static_event("/static/missing.js"), None)

    assert out["statusCode"] == 404


def test_app_storefront_bundled_fallback():
    from lib.static_assets import get_app_storefront_asset
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_static_event("/static/app-storefront.js"), None)

    assert out["statusCode"] == 200
    _, etag, _ = get_app_storefront_asset()
    assert out["headers"]["ETag"] == f'"{etag}"'
    assert "GwofyStorefront" in out["body"]


def test_if_none_match_304_uploaded():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {
        "Item": {
            "pk": "GLOBAL#STATIC_JS",
            "sk": "x.js",
            "source": "// x",
            "content_sha256": "deadbeef",
        }
    }

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_static_event("/static/x.js", if_none_match='"deadbeef"'), None)

    assert out["statusCode"] == 304

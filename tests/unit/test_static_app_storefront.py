"""GET/HEAD /static/app-storefront.js — public storefront script."""

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


def _static_event(method: str = "GET", if_none_match: str | None = None):
    headers: dict[str, str] = {}
    if if_none_match is not None:
        headers["if-none-match"] = if_none_match
    return {
        "requestContext": {"http": {"method": method, "path": "/static/app-storefront.js"}, "requestId": "r1"},
        "headers": headers,
    }


def test_get_app_storefront_js():
    from lib.static_assets import APP_STOREFRONT_VERSION, get_app_storefront_asset
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_static_event("GET"), None)
    assert out["statusCode"] == 200
    assert "javascript" in out["headers"]["Content-Type"]
    assert out["headers"]["X-Gwofy-Asset-Version"] == APP_STOREFRONT_VERSION
    assert out["headers"]["ETag"] == f'"{get_app_storefront_asset()[1]}"'
    assert "Gwofy Guard" in out["body"]
    assert "GwofyStorefront" in out["body"]


def test_head_app_storefront_js():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_static_event("HEAD"), None)
    assert out["statusCode"] == 200
    assert out["body"] == ""
    assert "ETag" in out["headers"]


def test_if_none_match_returns_304():
    from lib.static_assets import get_app_storefront_asset
    from merchant_api_handler import handler

    _, etag, _ = get_app_storefront_asset()
    tbl = MagicMock()
    tbl.get_item.return_value = {}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_static_event("GET", if_none_match=f'"{etag}"'), None)
    assert out["statusCode"] == 304
    assert out["body"] == ""


def test_static_route_does_not_require_bearer():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_static_event("GET"), None)
    assert out["statusCode"] == 200


def test_cart_config_response_has_no_storefront_script_fields():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "lambda/lib/cart_config_response.py"
    text = src.read_text(encoding="utf-8")
    assert "storefrontScriptUrl" not in text
    assert "storefrontVersion" not in text

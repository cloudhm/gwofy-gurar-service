"""GET/HEAD /static/app-config.js — per-shop public config script."""

import json
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
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://api.example.com")


def _event(method: str = "GET", shop: str | None = "gwo-dev.myshopify.com", shop_id_alias: bool = False):
    qs = {}
    if shop is not None:
        if shop_id_alias:
            qs["shopId"] = shop
        else:
            qs["shop"] = shop
    return {
        "requestContext": {"http": {"method": method, "path": "/static/app-config.js"}, "requestId": "r1"},
        "headers": {},
        "queryStringParameters": qs or None,
    }


def _activated_meta(**extra):
    return {
        "pk": "SHOP#gwo-dev.myshopify.com",
        "sk": "METADATA",
        "activation_status": "ACTIVATED",
        "updated_at": "2026-01-01T00:00:00Z",
        "shipping_protection_status": "OPEN_AUDITED",
        "shop_currency_code": "USD",
        "billing_country_code": "US",
        **extra,
    }


def test_get_app_config_js():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _activated_meta()}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_event("GET"), None)

    assert out["statusCode"] == 200
    assert "javascript" in out["headers"]["Content-Type"]
    assert "GWOFY_CONFIG" in out["body"]
    assert "gwo-dev.myshopify.com" in out["body"]
    assert out["headers"]["X-Gwofy-Asset-Version"] == "1.0.0"
    assert (
        '"remoteScriptUrls":["https://sp-prod.gwofy.com/static/app-storefront.js"]'
        in out["body"].replace(" ", "")
    )
    assert not out["body"].startswith("g.GWOFY_STOREFRONT_ASSET_URL")


def test_missing_shop():
    from merchant_api_handler import handler

    with patch("merchant_api_handler.ddb.Table", return_value=MagicMock()):
        out = handler(_event(shop=None), None)
    assert out["statusCode"] == 400
    assert json.loads(out["body"])["error"] == "missing_shop"


def test_invalid_shop_host():
    from merchant_api_handler import handler

    with patch("merchant_api_handler.ddb.Table", return_value=MagicMock()):
        out = handler(_event(shop="example.com"), None)
    assert out["statusCode"] == 400
    assert json.loads(out["body"])["error"] == "invalid_shop_host"


def test_shop_not_found():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_event(), None)
    assert out["statusCode"] == 404


def test_not_activated():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _activated_meta(activation_status="UNACTIVATED")}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_event(), None)
    assert out["statusCode"] == 403


def test_shop_id_query_alias():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _activated_meta()}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_event(shop_id_alias=True), None)
    assert out["statusCode"] == 200


def test_app_config_no_cache_control():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _activated_meta()}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_event(), None)

    assert out["statusCode"] == 200
    assert "Cache-Control" not in out["headers"]


def test_if_none_match_always_returns_200_body():
    from lib.static_assets import get_app_config_js_for_shop
    from lib.storefront_gwofy_config import build_effective_gwofy_config
    from merchant_api_handler import handler

    meta = _activated_meta()
    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": meta}
    merged = build_effective_gwofy_config(tbl, meta, "gwo-dev.myshopify.com")
    body, etag = get_app_config_js_for_shop(merged, "gwo-dev.myshopify.com", meta["updated_at"])

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(
            {
                **_event(),
                "headers": {"if-none-match": f'"{etag}"'},
            },
            None,
        )
    assert out["statusCode"] == 200
    assert "GWOFY_CONFIG" in out["body"]
    assert out["body"] == body


def test_static_route_does_not_require_bearer():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _activated_meta()}

    with patch("merchant_api_handler.ddb.Table", return_value=tbl):
        out = handler(_event(), None)
    assert out["statusCode"] == 200

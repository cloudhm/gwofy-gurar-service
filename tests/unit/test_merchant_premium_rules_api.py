"""GET/PUT /api/me/merchant-premium-rules."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# merchant_api_handler imports session_jwt → PyJWT; allow tests without PyJWT installed.
sys.modules.setdefault("jwt", MagicMock())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "sec")
    monkeypatch.setenv("KMS_KEY_ID", "kms")


def _session_event(method: str, path: str, body: str | None = None):
    ev = {
        "requestContext": {"http": {"method": method, "path": path}, "requestId": "r1"},
        "headers": {"authorization": "Bearer t"},
    }
    if body is not None:
        ev["body"] = body
    return ev


def test_put_merchant_premium_rules_persists():
    from merchant_api_handler import handler

    meta = {
        "pk": "SHOP#a.myshopify.com",
        "sk": "METADATA",
        "installation_status": "ACTIVE",
        "plugin_suspended": False,
    }
    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": meta}

    payload = {
        "version": 1,
        "markup": {"default": {"addPercent": 0, "addFixed": 0}, "byCountry": {}},
        "promotions": [],
    }

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
        patch("lib.merchant_premium_rules.is_country_supported", return_value=True),
    ):
        out = handler(
            _session_event("PUT", "/api/me/merchant-premium-rules", json.dumps(payload)),
            None,
        )
    assert out["statusCode"] == 200
    tbl.update_item.assert_called_once()
    kwargs = tbl.update_item.call_args.kwargs
    assert "merchant_premium_rules_json" in kwargs["ExpressionAttributeNames"].values()


def test_get_merchant_premium_rules():
    from merchant_api_handler import handler

    rules_json = json.dumps(
        {
            "version": 1,
            "markup": {"default": {"addPercent": 3, "addFixed": 0}, "byCountry": {}},
            "promotions": [],
        }
    )
    meta = {
        "pk": "SHOP#a.myshopify.com",
        "sk": "METADATA",
        "installation_status": "ACTIVE",
        "plugin_suspended": False,
        "merchant_premium_rules_json": rules_json,
    }
    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": meta}

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
        patch("lib.merchant_premium_rules.is_country_supported", return_value=True),
    ):
        out = handler(_session_event("GET", "/api/me/merchant-premium-rules"), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["merchantPremiumRules"]["markup"]["default"]["addPercent"] == 3.0

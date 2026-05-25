"""GET/PATCH /api/me/app-config."""

import json
import os
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


def _session_event(method: str, path: str, body: str | None = None):
    ev = {
        "requestContext": {"http": {"method": method, "path": path}, "requestId": "r1"},
        "headers": {"authorization": "Bearer t"},
    }
    if body is not None:
        ev["body"] = body
    return ev


def _active_meta(**extra):
    return {
        "pk": "SHOP#a.myshopify.com",
        "sk": "METADATA",
        "installation_status": "ACTIVE",
        "plugin_suspended": False,
        **extra,
    }


def test_get_app_config_empty():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _active_meta()}

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(_session_event("GET", "/api/me/app-config"), None)
    assert out["statusCode"] == 200
    assert json.loads(out["body"]) == {"appConfig": {}}


def test_get_app_config_with_coupon():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {
        "Item": _active_meta(merchant_app_config_json='{"couponCode":"SAVE10"}')
    }

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(_session_event("GET", "/api/me/app-config"), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["appConfig"]["couponCode"] == "SAVE10"


def test_patch_app_config_sets_coupon():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _active_meta()}

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(
            _session_event("PATCH", "/api/me/app-config", json.dumps({"couponCode": "WELCOME20"})),
            None,
        )
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["ok"] is True
    assert body["appConfig"]["couponCode"] == "WELCOME20"
    tbl.update_item.assert_called_once()
    kwargs = tbl.update_item.call_args.kwargs
    assert "merchant_app_config_json" in kwargs["ExpressionAttributeNames"].values()
    assert "WELCOME20" in kwargs["ExpressionAttributeValues"][":ac"]


def test_patch_app_config_removes_coupon():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {
        "Item": _active_meta(merchant_app_config_json='{"couponCode":"OLD"}')
    }

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(
            _session_event("PATCH", "/api/me/app-config", json.dumps({"couponCode": None})),
            None,
        )
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["appConfig"] == {}
    kwargs = tbl.update_item.call_args.kwargs
    assert "REMOVE" in kwargs["UpdateExpression"]


def test_patch_app_config_rejects_unknown_key():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _active_meta()}

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(
            _session_event("PATCH", "/api/me/app-config", json.dumps({"foo": "bar"})),
            None,
        )
    assert out["statusCode"] == 400
    body = json.loads(out["body"])
    assert body["error"] == "invalid_app_config"
    assert "couponCode" in body["allowed"]

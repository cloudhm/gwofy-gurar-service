"""GET/PUT /admin/shops/{shop}/storefront-config."""

import json
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("jwt", MagicMock())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")


def _admin_event(method: str, body: dict | None = None):
    ev = {
        "requestContext": {
            "http": {"method": method, "path": "/admin/shops/gwo-dev.myshopify.com/storefront-config"},
            "requestId": "r1",
            "authorizer": {"jwt": {"claims": {"sub": "admin1"}}}},
        "headers": {},
    }
    if body is not None:
        ev["body"] = json.dumps(body)
    return ev


@contextmanager
def _patch_derived_deps():
    with (
        patch("lib.storefront_gwofy_config.get_tips_info", return_value={"spVersion": {}}),
        patch(
            "lib.storefront_gwofy_config.shop_supported_currencies_list",
            return_value=["USD"],
        ),
    ):
        yield


def _meta():
    return {
        "pk": "SHOP#gwo-dev.myshopify.com",
        "sk": "METADATA",
        "shipping_protection_status": "OPEN_AUDITED",
        "shop_currency_code": "USD",
        "billing_country_code": "US",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_get_storefront_config():
    from admin_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _meta()}
    tbl.query.return_value = {"Items": []}

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
        _patch_derived_deps(),
    ):
        out = handler(_admin_event("GET"), None)

    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["shop"] == "gwo-dev.myshopify.com"
    assert "defaults" in body
    assert "effective" in body
    assert body["appConfigScriptName"] == "app-config.js"
    assert body["appConfigScripts"] == []


def test_put_storefront_config_text():
    from admin_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _meta()}
    tbl.query.return_value = {"Items": []}

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
        _patch_derived_deps(),
    ):
        out = handler(
            _admin_event("PUT", {"text": {"sp": {"title": "Protected shipping"}}}),
            None,
        )

    assert out["statusCode"] == 200
    tbl.update_item.assert_called_once()
    kwargs = tbl.update_item.call_args.kwargs
    assert "storefront_config_json" in kwargs["ExpressionAttributeNames"].values()


def test_put_accepts_pricing_calc_rate():
    from admin_handler import handler

    tbl = MagicMock()
    before = _meta()
    after = {
        **before,
        "storefront_config_json": '{"pricing":{"calcRate":"0.05"}}',
    }

    def fake_get_item(Key, **_kwargs):
        if Key.get("sk") == "METADATA":
            return {"Item": after}
        return {}

    tbl.get_item.side_effect = fake_get_item
    tbl.query.return_value = {"Items": []}

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
        _patch_derived_deps(),
    ):
        out = handler(_admin_event("PUT", {"pricing": {"calcRate": "0.05"}}), None)

    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["shopOverride"]["pricing"]["calcRate"] == "0.05"
    assert body["effective"]["pricing"]["calcRate"] == "0.05"

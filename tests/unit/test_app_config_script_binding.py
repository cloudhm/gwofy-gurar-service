"""App-config script upload, shop binding, and per-shop serve."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("jwt", MagicMock())

_VALID_TEMPLATE = (
    "/* custom */ g.GWOFY_CONFIG = Object.assign({}, /*__GWOFY_CONFIG_JSON__*/);"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "sec")
    monkeypatch.setenv("KMS_KEY_ID", "kms")
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://api.example.com")


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


def test_serve_custom_bound_app_config_template():
    from merchant_api_handler import handler

    meta = _activated_meta(app_config_script_name="app-config-v1.js")
    script_item = {
        "pk": "GLOBAL#STATIC_JS",
        "sk": "app-config-v1.js",
        "source": _VALID_TEMPLATE,
        "is_app_config": True,
        "content_sha256": "abc123",
        "byte_length": 100,
        "updated_at": "t",
        "updated_by": "admin",
    }

    def fake_get_item(Key, **_kwargs):
        if Key.get("sk") == "METADATA":
            return {"Item": meta}
        if Key.get("sk") == "app-config-v1.js":
            return {"Item": script_item}
        return {}

    tbl = MagicMock()
    tbl.get_item.side_effect = fake_get_item

    with (
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
        patch("lib.storefront_gwofy_config.get_tips_info", return_value={"spVersion": {}}),
        patch(
            "lib.storefront_gwofy_config.shop_supported_currencies_list",
            return_value=["USD"],
        ),
    ):
        out = handler(
            {
                "requestContext": {
                    "http": {"method": "GET", "path": "/static/app-config.js"},
                    "requestId": "r1",
                },
                "headers": {},
                "queryStringParameters": {"shop": "gwo-dev.myshopify.com"},
            },
            None,
        )

    assert out["statusCode"] == 200
    assert "/* custom */" in out["body"]
    assert "gwo-dev.myshopify.com" in out["body"]
    assert "/*__GWOFY_CONFIG_JSON__*/" not in out["body"]


def test_put_storefront_config_binds_app_config_script():
    from admin_handler import handler

    meta = _meta = _activated_meta()
    script_item = {
        "pk": "GLOBAL#STATIC_JS",
        "sk": "app-config-v1.js",
        "source": _VALID_TEMPLATE,
        "is_app_config": True,
        "content_sha256": "abc",
        "byte_length": 1,
        "updated_at": "t",
        "updated_by": "admin",
    }

    def fake_get_item(Key, **_kwargs):
        if Key.get("sk") == "METADATA":
            return {"Item": meta}
        if Key.get("sk") == "app-config-v1.js":
            return {"Item": script_item}
        return {}

    tbl = MagicMock()
    tbl.get_item.side_effect = fake_get_item
    tbl.query.return_value = {"Items": [script_item]}

    def fake_update_item(**_kwargs):
        meta["app_config_script_name"] = "app-config-v1.js"

    tbl.update_item.side_effect = fake_update_item

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
        patch("lib.storefront_gwofy_config.get_tips_info", return_value={"spVersion": {}}),
        patch(
            "lib.storefront_gwofy_config.shop_supported_currencies_list",
            return_value=["USD"],
        ),
    ):
        out = handler(
            {
                "requestContext": {
                    "http": {
                        "method": "PUT",
                        "path": "/admin/shops/gwo-dev.myshopify.com/storefront-config",
                    },
                    "requestId": "r1",
                    "authorizer": {"jwt": {"claims": {"sub": "admin1"}}},
                },
                "headers": {},
                "body": json.dumps({"appConfigScriptName": "app-config-v1.js"}),
            },
            None,
        )

    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["appConfigScriptName"] == "app-config-v1.js"
    tbl.update_item.assert_called_once()
    assert "app_config_script_name" in tbl.update_item.call_args.kwargs["ExpressionAttributeNames"].values()

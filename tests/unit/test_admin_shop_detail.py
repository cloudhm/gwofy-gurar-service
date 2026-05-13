"""GET /admin/shops/{shop}/detail includes merchantPremiumRules."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env_table(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")


def test_shop_detail_returns_merchant_premium_rules():
    from admin_handler import handler

    rules_obj = {
        "version": 1,
        "markup": {"default": {"addPercent": 2.0, "addFixed": 0.0}, "byCountry": {}},
        "promotions": [],
        "promotionApplyMode": "highest_threshold_wins",
    }
    meta = {
        "pk": "SHOP#x.myshopify.com",
        "sk": "METADATA",
        "shop": "x.myshopify.com",
        "access_token_enc": "blob",
        "merchant_premium_rules_json": json.dumps(rules_obj),
    }
    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": meta}

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(
            {
                "requestContext": {
                    "http": {"method": "GET", "path": "/admin/shops/x.myshopify.com/detail"},
                },
                "headers": {},
            },
            None,
        )
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["merchantPremiumRules"]["version"] == 1
    assert body["merchantPremiumRules"]["markup"]["default"]["addPercent"] == 2.0
    assert body["shop"]["access_token_enc"] == "blob"
    assert "merchant_premium_rules_parse_warning" not in body

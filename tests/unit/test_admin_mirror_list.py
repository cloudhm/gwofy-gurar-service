"""Admin product/order mirror list pagination and filters."""

import base64
import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")


@pytest.fixture(autouse=True)
def _env_table(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")


def _shop_meta():
    return {
        "pk": "SHOP#x.myshopify.com",
        "sk": "METADATA",
        "shop": "x.myshopify.com",
        "store_number": "10001",
    }


def _cursor_for(lek: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(lek, default=str).encode("ascii")).decode("ascii")


def test_list_orders_returns_next_cursor_and_filters():
    from admin_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _shop_meta()}
    lek = {"pk": "TENANT#10001", "sk": "ORDER#2"}
    tbl.query.return_value = {
        "Items": [{"sk": "ORDER#1", "order_name": "#1001", "has_shipping_protection": True}],
        "LastEvaluatedKey": lek,
    }

    with patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")), patch(
        "admin_handler.ddb.Table", return_value=tbl
    ):
        out = handler(
            {
                "requestContext": {"http": {"method": "GET", "path": "/admin/shops/x.myshopify.com/orders"}},
                "headers": {},
                "queryStringParameters": {
                    "only_protection": "true",
                    "order_name": "#1001",
                    "limit": "1",
                },
            },
            None,
        )

    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert len(body["items"]) == 1
    assert body["next_cursor"] == _cursor_for(lek)

    kwargs = tbl.query.call_args.kwargs
    assert "has_shipping_protection = :hp" in kwargs["FilterExpression"]
    assert "order_name = :on" in kwargs["FilterExpression"]
    assert kwargs["Limit"] == 1


def test_list_orders_invalid_date():
    from admin_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _shop_meta()}

    with patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")), patch(
        "admin_handler.ddb.Table", return_value=tbl
    ):
        out = handler(
            {
                "requestContext": {"http": {"method": "GET", "path": "/admin/shops/x.myshopify.com/orders"}},
                "headers": {},
                "queryStringParameters": {"created_from": "not-a-date"},
            },
            None,
        )

    assert out["statusCode"] == 400
    assert json.loads(out["body"])["error"] == "invalid_date"


def test_list_products_sku_filter_and_pagination():
    from admin_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": _shop_meta()}
    tbl.query.return_value = {
        "Items": [{"sk": "PRODUCT#1", "product_handle": "a", "variant_skus": {"SKU-A"}}],
        "LastEvaluatedKey": None,
    }

    with patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")), patch(
        "admin_handler.ddb.Table", return_value=tbl
    ):
        out = handler(
            {
                "requestContext": {"http": {"method": "GET", "path": "/admin/shops/x.myshopify.com/products"}},
                "headers": {},
                "queryStringParameters": {"sku": "SKU-A", "limit": "10"},
            },
            None,
        )

    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["items"][0]["product_handle"] == "a"
    assert body["next_cursor"] is None

    kwargs = tbl.query.call_args.kwargs
    assert "attribute_exists(product_handle)" in kwargs["FilterExpression"]
    assert "contains(variant_skus, :vsku)" in kwargs["FilterExpression"]

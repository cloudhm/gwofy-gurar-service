"""Denormalized mirror fields for admin list filters."""

import os
from decimal import Decimal

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from lib.sync_denorm import denorm_order_top_fields, denorm_product_top_fields


def test_denorm_product_variant_identifiers():
    snap = {
        "handle": "widget",
        "title": "Widget",
        "status": "ACTIVE",
        "variants": [
            {"sku": "SKU-1", "barcode": "B001", "price": "9.99", "metafields": [{"key": "amazon_asin", "value": "ASIN1"}]},
            {"sku": "SKU-2", "price": "12.00"},
        ],
        "product_metafields": [{"key": "parent_asin", "value": "ASIN2"}],
    }
    out = denorm_product_top_fields(snap)
    assert out["product_handle"] == "widget"
    assert out["variant_skus"] == {"SKU-1", "SKU-2"}
    assert out["variant_barcodes"] == {"B001", "ASIN1", "ASIN2"}
    assert out["price_min"] == Decimal("9.99")
    assert out["price_max"] == Decimal("12.00")


def test_denorm_order_top_fields():
    node = {
        "name": "#1001",
        "legacyResourceId": "1001",
        "createdAt": "2026-05-01T10:00:00Z",
        "processedAt": "2026-05-01T10:05:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "FULFILLED",
        "currentTotalPriceSet": {"shopMoney": {"amount": "42.50"}},
        "lineItems": {
            "edges": [
                {"node": {"sku": "LINE-SKU", "variant": {"sku": "VAR-SKU", "barcode": "BC1"}}},
            ]
        },
    }
    out = denorm_order_top_fields(node)
    assert out["order_name"] == "#1001"
    assert out["legacy_resource_id"] == "1001"
    assert out["order_created_at"] == "2026-05-01T10:00:00Z"
    assert out["order_processed_at"] == "2026-05-01T10:05:00Z"
    assert out["current_total_price"] == Decimal("42.50")
    assert out["line_item_skus"] == {"LINE-SKU", "VAR-SKU", "BC1"}

"""productCreate must not send deprecated `variants` on ProductCreateInput (Shopify 2025+)."""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from lib import protection_product as pp


def test_create_product_input_has_no_variants_field():
    create_vars: dict = {}

    def fake_gql(shop, token, query, variables, api_version="", **kwargs):
        if "productCreate" in query:
            create_vars.update(variables)
            return {
                "data": {
                    "productCreate": {
                        "product": {
                            "id": "gid://shopify/Product/1",
                            "variants": {
                                "nodes": [{"id": "gid://shopify/ProductVariant/1", "selectedOptions": []}]
                            },
                        },
                        "userErrors": [],
                    }
                }
            }
        if "productVariantsBulkUpdate" in query:
            return {"data": {"productVariantsBulkUpdate": {"userErrors": []}}}
        raise AssertionError(f"unexpected query: {query[:80]}")

    tiers = [("S0001", Decimal("0.89"), "S0001"), ("S0002", Decimal("1.99"), "S0002")]

    with (
        patch.object(pp, "_protection_gql", side_effect=fake_gql),
        patch.object(pp, "_publish_product_to_online_store"),
    ):
        with patch.object(pp, "_bulk_create_chunks") as bulk:
            gid = pp._create_new_protection_product(
                "s.myshopify.com",
                "tok",
                "2026-04",
                tiers,
                "Shipping Protection",
                "GWOFY",
                "shipping-protection",
                "GWOFY-SHIPPING-PROTECTION-QAQWER",
            )

    assert gid == "gid://shopify/Product/1"
    product = create_vars["product"]
    assert "variants" not in product
    assert product["productOptions"] == [
        {"name": "Plan", "values": [{"name": "S0001"}, {"name": "S0002"}]}
    ]
    bulk.assert_called_once()

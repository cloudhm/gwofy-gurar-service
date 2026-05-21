"""Protection product must publish to Online Store channel."""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from lib import protection_product as pp


def test_create_publishes_to_online_store_publication():
    publish_calls: list[str] = []

    def fake_gql(shop, token, query, variables, api_version="", **kwargs):
        if "publications(first" in query:
            return {
                "data": {
                    "publications": {
                        "nodes": [
                            {
                                "id": "gid://shopify/Publication/99",
                                "catalog": {"title": "Online Store"},
                                "channels": {"nodes": [{"name": "Online Store"}]},
                            }
                        ]
                    }
                }
            }
        if "productCreate" in query:
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
        if "publishablePublish" in query:
            publish_calls.append(variables["id"])
            assert variables["input"] == [{"publicationId": "gid://shopify/Publication/99"}]
            return {"data": {"publishablePublish": {"userErrors": []}}}
        raise AssertionError(f"unexpected query: {query[:80]}")

    tiers = [("S0001", Decimal("0.89"), "S0001")]

    with patch.object(pp, "_protection_gql", side_effect=fake_gql):
        with patch.object(pp, "_bulk_create_chunks"):
            gid = pp._create_new_protection_product(
                "s.myshopify.com",
                "tok",
                "2026-04",
                tiers,
                "Shipping Protection",
                "GWOFY",
                "shipping-protection",
                None,
            )

    assert gid == "gid://shopify/Product/1"
    assert publish_calls == ["gid://shopify/Product/1"]


def test_update_publishes_to_online_store_publication():
    publish_calls: list[str] = []

    def fake_gql(shop, token, query, variables, api_version="", **kwargs):
        if "publications(first" in query:
            return {
                "data": {
                    "publications": {
                        "nodes": [
                            {
                                "id": "gid://shopify/Publication/99",
                                "catalog": {"title": "Other"},
                                "channels": {"nodes": [{"name": "Online Store"}]},
                            }
                        ]
                    }
                }
            }
        if "productUpdate" in query:
            return {"data": {"productUpdate": {"userErrors": []}}}
        if "publishablePublish" in query:
            publish_calls.append(variables["id"])
            return {"data": {"publishablePublish": {"userErrors": []}}}
        raise AssertionError(f"unexpected query: {query[:80]}")

    tiers = [("S0001", Decimal("0.89"), "S0001")]

    with (
        patch.object(pp, "_list_all_variant_nodes", return_value=[]),
        patch.object(pp, "_bulk_create_chunks"),
        patch.object(pp, "_protection_gql", side_effect=fake_gql),
    ):
        gid = pp._apply_tiers_to_existing_product(
            "s.myshopify.com",
            "tok",
            "2026-04",
            "gid://shopify/Product/42",
            tiers,
            "GWOFY",
        )

    assert gid == "gid://shopify/Product/42"
    assert publish_calls == ["gid://shopify/Product/42"]

"""Embed deep link URL builder."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda"))

from unittest.mock import MagicMock

from lib.embed_deep_link import (
    build_embed_deep_link,
    resolve_main_theme_gid,
    shop_handle_from_host,
    theme_numeric_id_from_gid,
)


def test_shop_handle_from_myshopify():
    assert shop_handle_from_host("Gwofy-DEV2.myshopify.com") == "gwofy-dev2"


def test_theme_numeric_id_from_gid():
    gid = "gid://shopify/OnlineStoreTheme/225007463"
    assert theme_numeric_id_from_gid(gid) == "225007463"


def test_build_embed_deep_link_from_metadata():
    meta = {"main_theme_gid": "gid://shopify/OnlineStoreTheme/529529152"}
    url = build_embed_deep_link("gwofy-dev.myshopify.com", meta)
    assert url == (
        "https://admin.shopify.com/store/gwofy-dev/themes/529529152/editor"
        "?context=apps&appEmbed=9b094559e27d2b0b68a5c0c0b4743f73%2Finsurance"
        "&previewPath=%2Fcart"
    )


def test_build_embed_deep_link_empty_without_main_theme():
    assert build_embed_deep_link("a.myshopify.com", {}) == ""


def test_resolve_main_theme_gid_from_table():
    table = MagicMock()
    table.query.return_value = {
        "Items": [
            {
                "sk": "THEME#gid://shopify/OnlineStoreTheme/2",
                "role": "UNPUBLISHED",
                "shopify_id": "gid://shopify/OnlineStoreTheme/2",
            },
            {
                "sk": "THEME#gid://shopify/OnlineStoreTheme/1",
                "role": "MAIN",
                "shopify_id": "gid://shopify/OnlineStoreTheme/1",
            },
        ]
    }
    gid = resolve_main_theme_gid({}, table, "shop.myshopify.com")
    assert gid == "gid://shopify/OnlineStoreTheme/1"

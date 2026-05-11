import os

from lib.sync_order_tags import order_sync_tags


def test_order_sync_tags_empty_without_protection(monkeypatch):
    monkeypatch.delenv("ORDER_PROTECTION_TAG", raising=False)
    assert order_sync_tags(False, "gid://shopify/Product/1") == []


def test_order_sync_tags_empty_without_product_gid(monkeypatch):
    monkeypatch.delenv("ORDER_PROTECTION_TAG", raising=False)
    assert order_sync_tags(True, None) == []
    assert order_sync_tags(True, "") == []


def test_order_sync_tags_default_tag(monkeypatch):
    monkeypatch.delenv("ORDER_PROTECTION_TAG", raising=False)
    assert order_sync_tags(True, "gid://shopify/Product/1") == ["gwofy-shipping-protection"]


def test_order_sync_tags_env_override(monkeypatch):
    monkeypatch.setenv("ORDER_PROTECTION_TAG", "my-label")
    assert order_sync_tags(True, "gid://shopify/Product/1") == ["my-label"]

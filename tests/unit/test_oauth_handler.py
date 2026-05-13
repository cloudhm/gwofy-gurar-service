"""OAuth callback helpers."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

import pytest

from oauth_handler import _post_install_redirect_location, _shop_admin_slug


def test_shop_admin_slug_strips_myshopify():
    assert _shop_admin_slug("gwofy-dev2.myshopify.com") == "gwofy-dev2"


def test_shop_admin_slug_trims_and_lower():
    assert _shop_admin_slug("  Gwofy-DEV2.MYSHOPIFY.COM/ ") == "gwofy-dev2"


def test_shop_admin_slug_non_myshopify_unchanged():
    assert _shop_admin_slug("custom.example.com") == "custom.example.com"


def test_post_install_redirect_default(monkeypatch):
    monkeypatch.delenv("POST_INSTALL_REDIRECT_URL", raising=False)
    url = _post_install_redirect_location("gwofy-dev2.myshopify.com")
    assert url == "https://admin.shopify.com/store/gwofy-dev2/apps/gwofy-guard"


def test_post_install_redirect_env_override(monkeypatch):
    monkeypatch.setenv("POST_INSTALL_REDIRECT_URL", "https://example.com/done")
    assert _post_install_redirect_location("gwofy-dev2.myshopify.com") == "https://example.com/done"


def test_post_install_redirect_env_empty_uses_default(monkeypatch):
    monkeypatch.setenv("POST_INSTALL_REDIRECT_URL", "   ")
    url = _post_install_redirect_location("a.myshopify.com")
    assert url == "https://admin.shopify.com/store/a/apps/gwofy-guard"

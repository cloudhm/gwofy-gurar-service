"""OAuth callback helpers."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

import pytest

from oauth_handler import _post_install_redirect_location


def test_post_install_redirect_default(monkeypatch):
    monkeypatch.delenv("POST_INSTALL_REDIRECT_URL", raising=False)
    url = _post_install_redirect_location("gwofy-dev2.myshopify.com", "abc123client")
    assert url == "https://gwofy-dev2.myshopify.com/admin/apps/abc123client"


def test_post_install_redirect_normalizes_shop_host(monkeypatch):
    monkeypatch.delenv("POST_INSTALL_REDIRECT_URL", raising=False)
    url = _post_install_redirect_location("  Gwofy-DEV2.MYSHOPIFY.COM/ ", "cid")
    assert url == "https://gwofy-dev2.myshopify.com/admin/apps/cid"


def test_post_install_redirect_env_override(monkeypatch):
    monkeypatch.setenv("POST_INSTALL_REDIRECT_URL", "https://example.com/done")
    assert (
        _post_install_redirect_location("gwofy-dev2.myshopify.com", "ignored")
        == "https://example.com/done"
    )


def test_post_install_redirect_env_empty_uses_default(monkeypatch):
    monkeypatch.setenv("POST_INSTALL_REDIRECT_URL", "   ")
    url = _post_install_redirect_location("a.myshopify.com", "app-key-1")
    assert url == "https://a.myshopify.com/admin/apps/app-key-1"

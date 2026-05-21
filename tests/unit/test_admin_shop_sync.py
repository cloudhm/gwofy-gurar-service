"""Admin shop sync resource normalization."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from lib.admin_shop_sync import normalize_resources


def test_normalize_all_expands():
    assert normalize_resources(["all"]) == [
        "shop_profile",
        "products",
        "orders",
        "currencies",
        "markets",
        "themes",
    ]


def test_normalize_themes():
    assert normalize_resources(["theme"]) == ["themes"]


def test_normalize_catalog():
    assert normalize_resources(["catalog"]) == ["products", "orders"]


def test_normalize_unknown_raises():
    import pytest

    with pytest.raises(ValueError, match="unknown_resource"):
        normalize_resources(["nope"])

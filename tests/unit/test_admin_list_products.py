"""Admin product list sk filtering (DynamoDB cannot filter on sk)."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from admin_handler import _is_primary_product_mirror_sk


def test_primary_product_sk():
    assert _is_primary_product_mirror_sk("PRODUCT#gid://shopify/Product/1")
    assert not _is_primary_product_mirror_sk("PRODUCT#gid://shopify/Product/1#META_VER#20260515")
    assert not _is_primary_product_mirror_sk("VARIANT#gid://shopify/ProductVariant/1")

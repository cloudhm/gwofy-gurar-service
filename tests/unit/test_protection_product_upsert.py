"""upsert_protection_product: adopt existing handle on first activation."""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from lib import protection_product as pp


def test_first_activation_adopts_existing_handle_instead_of_error():
    existing_gid = "gid://shopify/Product/99"
    tiers = [("S0001", Decimal("0.89"), "S0001")]

    with (
        patch.object(pp, "_first_product_gid_by_handle", return_value=existing_gid) as by_handle,
        patch.object(pp, "_product_id_exists", return_value=False),
        patch.object(
            pp,
            "_apply_tiers_to_existing_product",
            return_value=existing_gid,
        ) as apply_tiers,
        patch.object(pp, "_create_new_protection_product") as create_new,
    ):
        gid = pp.upsert_protection_product(
            "s.myshopify.com",
            "tok",
            "2026-04",
            existing_product_gid=None,
            tiers_shop=tiers,
            title="Shipping Protection",
            vendor="GWOFY",
            product_type="shipping-protection",
            handle="GWOFY-SHIPPING-PROTECTION-QAQWER",
        )

    assert gid == existing_gid
    by_handle.assert_called_once()
    apply_tiers.assert_called_once()
    create_new.assert_not_called()

from decimal import Decimal

from lib.default_protection_tiers import (
    DEFAULT_VARIANT_USD_PRICES,
    PROTECTION_PRODUCT_HANDLE,
    build_default_tiers,
)
from lib.pricing_resolve import pick_tier


def test_default_variant_price_count():
    assert len(DEFAULT_VARIANT_USD_PRICES) == 98


def test_build_default_tiers_count_and_shape():
    tiers = build_default_tiers()
    assert len(tiers) == 98
    assert tiers[0]["plan_code"] == "S0001"
    assert tiers[-1]["plan_code"] == "S0098"
    assert float(tiers[0]["price"]) == 0.98
    assert float(tiers[-1]["price"]) == 359.0
    assert "min_usd" not in tiers[0]
    assert "sku" not in tiers[0]


def test_pick_tier_zero_cart_maps_first_variant():
    tiers = build_default_tiers()
    t = pick_tier(tiers, Decimal("0"), coverage_max_usd=9000)
    assert t and t["plan_code"] == "S0001"


def test_protection_handle_constant():
    assert "SHIPPING-PROTECTION" in PROTECTION_PRODUCT_HANDLE

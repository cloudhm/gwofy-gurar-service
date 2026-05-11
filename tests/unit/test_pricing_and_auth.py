from decimal import Decimal

from lib.order_protection import order_has_protection_product
from lib.pricing_resolve import pick_tier, shop_to_usd
from lib.storefront_auth import storefront_hmac_sign, verify_shop_body_hmac


def test_pick_tier_mid_range():
    tiers = [
        {"plan_code": "S001", "min_usd": 10.0, "max_usd": 100.0, "price_usd": 1.0},
        {"plan_code": "S002", "min_usd": 100.0, "max_usd": 200.0, "price_usd": 2.0},
    ]
    t = pick_tier(tiers, Decimal("50"))
    assert t and t["plan_code"] == "S001"


def test_shop_to_usd():
    assert shop_to_usd(Decimal("11"), 1.1) == Decimal("10")


def test_hmac_roundtrip():
    secret = "testsecret"
    shop = "a.myshopify.com"
    sig = storefront_hmac_sign(secret, shop, {"cart_subtotal": "99", "currency": "USD"})
    canonical = '{"cart_subtotal":"99","currency":"USD"}'
    assert verify_shop_body_hmac(secret, shop, canonical, sig) is True
    assert verify_shop_body_hmac(secret, shop, '{"currency":"USD","cart_subtotal":"99"}', sig) is False


def test_order_has_protection():
    gid = "gid://shopify/Product/1"
    order = {
        "lineItems": {
            "edges": [
                {"node": {"product": {"id": "gid://shopify/Product/2"}}},
                {"node": {"product": {"id": gid}}},
            ]
        }
    }
    assert order_has_protection_product(order, gid) is True
    assert order_has_protection_product(order, "gid://shopify/Product/99") is False

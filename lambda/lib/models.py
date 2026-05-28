"""DynamoDB key helpers."""

from __future__ import annotations

PK_GLOBAL_CONFIG = "GLOBAL#CONFIG"
SK_PRICING_MODEL_DEFAULT = "PRICING_MODEL_DEFAULT"
SK_SUPPORTED_CURRENCIES = "SUPPORTED_CURRENCIES"
SK_SHIPPING_COUNTRY_DEFAULTS = "SHIPPING_COUNTRY_DEFAULTS"
SK_MAX_COVERAGE_BY_CURRENCY = "MAX_COVERAGE_BY_CURRENCY"
SK_ACTIVITY_INFO = "ACTIVITY_INFO"
SK_TIPS_INFO = "TIPS_INFO"
SK_CALC_COVERAGE_TIPS = "CALC_COVERAGE_TIPS"

# Shop METADATA overrides for cart calc tips (inherit global when absent).
META_SP_BELOW_MIN_COVERAGE_TIP = "sp_below_min_coverage_tip"
META_SP_GREATER_MAX_COVERAGE_TIP = "sp_greater_max_coverage_tip"
MERCHANT_PREMIUM_RULES_JSON = "merchant_premium_rules_json"
MERCHANT_APP_CONFIG_JSON = "merchant_app_config_json"
STOREFRONT_CONFIG_JSON = "storefront_config_json"
META_PROTECTION_PRODUCT_HANDLE = "protection_product_handle"
GSI2_PK_SHOP_INDEX = "SHOP_INDEX"
SK_AUDIT_PREFIX = "AUDIT#"


def pk_shop(shop: str) -> str:
    return f"SHOP#{shop.strip().lower().rstrip('/')}"


def sk_pricing_model(currency: str) -> str:
    """Per-currency tier ladder in GLOBAL#CONFIG (native amounts in `price`)."""
    return f"PRICING_MODEL#{currency.strip().upper()}"


def pk_store_number(num: str) -> str:
    return f"STORE_NUMBER#{num}"


def pk_tenant(store_number: str) -> str:
    return f"TENANT#{store_number}"


def pk_sync(shop: str) -> str:
    return f"SYNC#{shop.strip().lower().rstrip('/')}"


def pk_webhook(webhook_id: str) -> str:
    return f"WEBHOOK#{webhook_id}"


# One row per processed HTTPS webhook delivery (X-Shopify-Webhook-Id).
SK_WEBHOOK_PROCESSED = "PROCESSED"

SK_METADATA = "METADATA"

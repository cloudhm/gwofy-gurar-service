"""DynamoDB key helpers."""

from __future__ import annotations

PK_GLOBAL_CONFIG = "GLOBAL#CONFIG"
SK_PRICING_MODEL_DEFAULT = "PRICING_MODEL_DEFAULT"
SK_SHIPPING_COUNTRY_DEFAULTS = "SHIPPING_COUNTRY_DEFAULTS"
GSI2_PK_SHOP_INDEX = "SHOP_INDEX"
SK_AUDIT_PREFIX = "AUDIT#"


def pk_shop(shop: str) -> str:
    return f"SHOP#{shop.strip().lower().rstrip('/')}"


def pk_store_number(num: str) -> str:
    return f"STORE_NUMBER#{num}"


def pk_tenant(store_number: str) -> str:
    return f"TENANT#{store_number}"


def pk_sync(shop: str) -> str:
    return f"SYNC#{shop.strip().lower().rstrip('/')}"


def pk_webhook(webhook_id: str) -> str:
    return f"WEBHOOK#{webhook_id}"


SK_METADATA = "METADATA"

"""DynamoDB key helpers."""

from __future__ import annotations


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

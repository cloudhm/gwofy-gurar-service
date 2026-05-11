"""Labels stored on mirrored order rows in DynamoDB (not written to Shopify)."""

from __future__ import annotations

import os


def order_sync_tags(has_shipping_protection: bool, protection_product_gid: str | None) -> list[str]:
    """Return tags to persist on our ORDER# item when we know the shop's protection product GID."""
    if not has_shipping_protection:
        return []
    if not (protection_product_gid or "").strip():
        return []
    tag = (os.environ.get("ORDER_PROTECTION_TAG") or "gwofy-shipping-protection").strip()
    return [tag] if tag else []

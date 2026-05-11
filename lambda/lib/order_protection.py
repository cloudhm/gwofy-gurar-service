"""Detect protection product on order GraphQL node."""

from __future__ import annotations

from typing import Any


def order_has_protection_product(order_node: dict[str, Any], protection_product_gid: str | None) -> bool:
    if not protection_product_gid:
        return False
    edges = ((order_node.get("lineItems") or {}).get("edges")) or []
    for e in edges:
        n = e.get("node") or {}
        pid = ((n.get("product") or {}).get("id")) or ""
        if pid == protection_product_gid:
            return True
    return False

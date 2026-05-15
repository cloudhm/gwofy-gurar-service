"""Denormalized top-level fields on PRODUCT#/ORDER# rows for admin filtering."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _money_amount(set_field: Any) -> str | None:
    """Extract shopMoney.amount from MoneyBag-like GraphQL response."""
    if not isinstance(set_field, dict):
        return None
    shop = set_field.get("shopMoney") or {}
    amt = shop.get("amount")
    if amt is None:
        return None
    return str(amt)


def denorm_product_top_fields(snap: dict[str, Any]) -> dict[str, Any]:
    """Scalar attrs from product snapshot for Dynamo filtering."""
    variants = snap.get("variants") or []
    prices: list[Decimal] = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        p = v.get("price")
        if p is None:
            continue
        try:
            prices.append(Decimal(str(p)))
        except Exception:
            continue
    min_p = min(prices) if prices else None
    max_p = max(prices) if prices else None
    out: dict[str, Any] = {
        "product_handle": str(snap.get("handle") or ""),
        "product_title": str(snap.get("title") or "")[:500],
        "product_status": str(snap.get("status") or ""),
        "variant_count": len(variants),
        "sync_deleted": False,
        "deleted_at": None,
    }
    if min_p is not None:
        out["price_min"] = min_p
    if max_p is not None:
        out["price_max"] = max_p
    return out


def denorm_order_top_fields(node: dict[str, Any]) -> dict[str, Any]:
    """Scalar attrs from order GraphQL node for Dynamo filtering."""
    cur_set = node.get("currentTotalPriceSet")
    amt = _money_amount(cur_set)
    dfs = node.get("displayFinancialStatus")
    dful = node.get("displayFulfillmentStatus")
    out: dict[str, Any] = {
        "order_name": str(node.get("name") or ""),
        "legacy_resource_id": str(node.get("legacyResourceId") or ""),
        "display_financial_status": dfs if isinstance(dfs, str) else str(dfs or ""),
        "display_fulfillment_status": dful if isinstance(dful, str) else str(dful or ""),
        "sync_deleted": False,
        "deleted_at": None,
    }
    if amt is not None:
        try:
            out["current_total_price"] = Decimal(amt)
        except Exception:
            out["current_total_price_raw"] = amt
    return out



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


def _asin_from_metafields(metafields: Any) -> set[str]:
    out: set[str] = set()
    if not isinstance(metafields, list):
        return out
    for mf in metafields:
        if not isinstance(mf, dict):
            continue
        key = str(mf.get("key") or "").lower()
        val = str(mf.get("value") or "").strip()
        if val and "asin" in key:
            out.add(val)
    return out


def _collect_product_variant_identifiers(snap: dict[str, Any]) -> tuple[set[str], set[str]]:
    skus: set[str] = set()
    barcodes: set[str] = set()
    for v in snap.get("variants") or []:
        if not isinstance(v, dict):
            continue
        if s := str(v.get("sku") or "").strip():
            skus.add(s)
        if b := str(v.get("barcode") or "").strip():
            barcodes.add(b)
        barcodes |= _asin_from_metafields(v.get("metafields"))
    barcodes |= _asin_from_metafields(snap.get("product_metafields"))
    return skus, barcodes


def _collect_order_line_skus(node: dict[str, Any]) -> set[str]:
    skus: set[str] = set()
    for edge in (node.get("lineItems") or {}).get("edges") or []:
        n = edge.get("node") or {}
        if not isinstance(n, dict):
            continue
        if s := str(n.get("sku") or "").strip():
            skus.add(s)
        variant = n.get("variant") or {}
        if isinstance(variant, dict):
            if s := str(variant.get("sku") or "").strip():
                skus.add(s)
            if b := str(variant.get("barcode") or "").strip():
                skus.add(b)
    return skus


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
    skus, barcodes = _collect_product_variant_identifiers(snap)
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
    if skus:
        out["variant_skus"] = skus
    if barcodes:
        out["variant_barcodes"] = barcodes
    return out


def denorm_order_top_fields(node: dict[str, Any]) -> dict[str, Any]:
    """Scalar attrs from order GraphQL node for Dynamo filtering."""
    cur_set = node.get("currentTotalPriceSet")
    amt = _money_amount(cur_set)
    dfs = node.get("displayFinancialStatus")
    dful = node.get("displayFulfillmentStatus")
    line_skus = _collect_order_line_skus(node)
    out: dict[str, Any] = {
        "order_name": str(node.get("name") or ""),
        "legacy_resource_id": str(node.get("legacyResourceId") or ""),
        "display_financial_status": dfs if isinstance(dfs, str) else str(dfs or ""),
        "display_fulfillment_status": dful if isinstance(dful, str) else str(dful or ""),
        "sync_deleted": False,
        "deleted_at": None,
    }
    if ca := node.get("createdAt"):
        out["order_created_at"] = str(ca)
    if pa := node.get("processedAt"):
        out["order_processed_at"] = str(pa)
    if amt is not None:
        try:
            out["current_total_price"] = Decimal(amt)
        except Exception:
            out["current_total_price_raw"] = amt
    if line_skus:
        out["line_item_skus"] = line_skus
    return out


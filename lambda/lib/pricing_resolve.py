"""Map cart subtotal to tier (USD boundaries in config; cart in shop currency)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def shop_to_usd(amount_shop: Decimal, usd_to_shop_rate: float) -> Decimal:
    if usd_to_shop_rate <= 0:
        raise ValueError("invalid_fx_rate")
    return amount_shop / Decimal(str(usd_to_shop_rate))


def _tiers_have_explicit_bands(tiers: list[dict[str, Any]]) -> bool:
    """Legacy Dynamo rows may store min_usd/max_usd per tier."""
    return bool(tiers) and any("min_usd" in t for t in tiers)


def _pick_tier_explicit_bands(tiers: list[dict[str, Any]], cart_subtotal_usd: Decimal) -> dict[str, Any] | None:
    """min_usd inclusive, max_usd exclusive; last tier unbounded above."""
    for t in sorted(tiers, key=lambda x: float(x.get("min_usd", 0))):
        lo = Decimal(str(t.get("min_usd", 0)))
        hi = Decimal(str(t.get("max_usd", 10**18)))
        if cart_subtotal_usd >= lo and cart_subtotal_usd < hi:
            return t
    if tiers:
        last = sorted(tiers, key=lambda x: float(x.get("min_usd", 0)))[-1]
        lo = Decimal(str(last.get("min_usd", 0)))
        if cart_subtotal_usd >= lo:
            return last
    return None


def _pick_tier_equal_split(
    tiers: list[dict[str, Any]],
    cart_subtotal_usd: Decimal,
    coverage_max_usd: Decimal,
) -> dict[str, Any] | None:
    """Equal-width bands on [0, coverage_max); last tier catches the top segment."""
    n = len(tiers)
    if n == 0:
        return None
    cap = coverage_max_usd if coverage_max_usd > 0 else Decimal("0")
    for i, t in enumerate(tiers):
        lo = cap * Decimal(i) / Decimal(n)
        if i < n - 1:
            hi = cap * Decimal(i + 1) / Decimal(n)
            if cart_subtotal_usd >= lo and cart_subtotal_usd < hi:
                return t
        elif cart_subtotal_usd >= lo:
            return t
    return None


def pick_tier(
    tiers: list[dict[str, Any]],
    cart_subtotal_usd: Decimal,
    *,
    coverage_max_usd: Decimal | float | None = None,
) -> dict[str, Any] | None:
    """Resolve tier by cart USD subtotal.

    If stored tiers include ``min_usd`` (legacy), use explicit bands.

    Otherwise partition ``[0, coverage_max_usd)`` into equal bands by **array order**.
    """
    if not tiers:
        return None
    if _tiers_have_explicit_bands(tiers):
        return _pick_tier_explicit_bands(tiers, cart_subtotal_usd)
    cap = Decimal(str(coverage_max_usd if coverage_max_usd is not None else "9000"))
    return _pick_tier_equal_split(tiers, cart_subtotal_usd, cap)


def format_money(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.01"))
    return format(q, "f")

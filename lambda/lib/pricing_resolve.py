"""Map cart subtotal to tier (USD boundaries in config; cart in shop currency)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def shop_to_usd(amount_shop: Decimal, usd_to_shop_rate: float) -> Decimal:
    if usd_to_shop_rate <= 0:
        raise ValueError("invalid_fx_rate")
    return amount_shop / Decimal(str(usd_to_shop_rate))


def pick_tier(tiers: list[dict[str, Any]], cart_subtotal_usd: Decimal) -> dict[str, Any] | None:
    """Tiers: min_usd inclusive, max_usd exclusive (last tier: treat max as inclusive by using large max)."""
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


def format_money(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.01"))
    return format(q, "f")

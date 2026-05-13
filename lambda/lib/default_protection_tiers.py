"""Default 98-variant shipping protection template (USD addon + cart subtotal bands)."""

from __future__ import annotations

from typing import Any

# Fixed storefront / GraphQL product handle (not the same as Shopify storefront URL handle uniqueness).
PROTECTION_PRODUCT_HANDLE = "GWOFY-SHIPPING-PROTECTION-QAQWER"

# Vendor shown on the Shipping Protection product (create + productUpdate on every upsert).
PROTECTION_PRODUCT_VENDOR = "GWOFY"

# Default max cart coverage (USD) used to partition tiers; per-shop override in METADATA `sp_max_coverage_usd`.
DEFAULT_COVERAGE_MAX_USD = 9000.0

# 98 addon prices (USD) — merchant template; admin may replace via PUT /admin/config/pricing-model.
DEFAULT_VARIANT_USD_PRICES: tuple[str, ...] = (
    "0.98",
    "1.15",
    "1.45",
    "1.75",
    "2.05",
    "2.35",
    "2.65",
    "2.95",
    "3.25",
    "3.55",
    "3.85",
    "4.15",
    "4.45",
    "4.75",
    "5.05",
    "5.35",
    "5.65",
    "5.95",
    "6.25",
    "6.55",
    "6.85",
    "7.15",
    "7.45",
    "7.75",
    "8.05",
    "8.35",
    "8.65",
    "8.95",
    "9.25",
    "9.55",
    "9.85",
    "10.15",
    "10.45",
    "10.75",
    "11.05",
    "11.35",
    "11.65",
    "11.95",
    "12.25",
    "12.88",
    "13.48",
    "14.23",
    "14.98",
    "15.73",
    "16.48",
    "17.23",
    "17.98",
    "18.73",
    "19.48",
    "20.23",
    "20.98",
    "21.73",
    "22.48",
    "23.23",
    "23.98",
    "26.53",
    "29.08",
    "31.63",
    "34.18",
    "36.73",
    "39.28",
    "41.83",
    "44.38",
    "46.93",
    "49.48",
    "55.03",
    "60.58",
    "66.13",
    "71.68",
    "77.23",
    "82.78",
    "88.33",
    "93.88",
    "99.43",
    "105.00",
    "114.00",
    "122.00",
    "131.00",
    "139.00",
    "148.00",
    "156.00",
    "165.00",
    "173.00",
    "182.00",
    "190.00",
    "202.00",
    "214.00",
    "225.00",
    "237.00",
    "248.00",
    "260.00",
    "271.00",
    "283.00",
    "294.00",
    "309.00",
    "325.00",
    "341.00",
    "359.00",
)

assert len(DEFAULT_VARIANT_USD_PRICES) == 98


def build_default_tiers(
    _coverage_max_usd: float = DEFAULT_COVERAGE_MAX_USD,
) -> list[dict[str, Any]]:
    """Cart USD bands are derived at resolve time from tier order and effective coverage max (see pricing_resolve)."""
    tiers: list[dict[str, Any]] = []
    for i, price_s in enumerate(DEFAULT_VARIANT_USD_PRICES):
        plan = f"S{i + 1:04d}"
        tiers.append(
            {
                "plan_code": plan,
                "price": float(price_s),
            }
        )
    return tiers

"""Shop enabled presentment currencies from Shopify Admin API."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .models import SK_METADATA, pk_shop
from .pricing_currencies import ALLOWED_PRICING_CURRENCIES, normalize_currency_code
from .shop_offline_access import ShopAdminAuth, shop_admin_graphql_call

logger = logging.getLogger(__name__)

CURRENCY_QUERY = """
query ShopEnabledCurrencies {
  shop {
    currencyCode
    currencySettings(first: 50) {
      edges {
        node {
          currencyCode
          enabled
        }
      }
    }
  }
}
"""


def parse_shop_enabled_currencies_json(meta: dict[str, Any]) -> frozenset[str]:
    raw = meta.get("shop_enabled_currencies_json")
    if not isinstance(raw, str):
        return frozenset()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return frozenset()
    if not isinstance(data, list):
        return frozenset()
    out: set[str] = set()
    for x in data:
        cy = normalize_currency_code(str(x))
        if len(cy) == 3 and cy.isalpha() and cy in ALLOWED_PRICING_CURRENCIES:
            out.add(cy)
    return frozenset(out)


def _collect_from_graphql_payload(data: dict[str, Any]) -> list[str]:
    s = data.get("data", {}).get("shop") or {}
    primary = normalize_currency_code(str(s.get("currencyCode") or ""))
    codes: set[str] = set()
    if primary and len(primary) == 3 and primary.isalpha():
        codes.add(primary)
    edges = ((s.get("currencySettings") or {}).get("edges")) or []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node") or {}
        if not node.get("enabled"):
            continue
        cy = normalize_currency_code(str(node.get("currencyCode") or ""))
        if len(cy) == 3 and cy.isalpha():
            codes.add(cy)
    allowed = [c for c in sorted(codes) if c in ALLOWED_PRICING_CURRENCIES]
    if not allowed and primary in ALLOWED_PRICING_CURRENCIES:
        return [primary]
    return allowed if allowed else ([primary] if primary in ALLOWED_PRICING_CURRENCIES else [])


def sync_shop_enabled_currencies(
    table,
    shop: str,
    token: str,
    api_version: str,
    *,
    fallback_primary: str | None = None,
    auth: ShopAdminAuth | None = None,
) -> list[str]:
    """Fetch enabled currencies from Shopify and persist to METADATA. Returns sorted list."""
    shop_norm = shop.strip().lower().rstrip("/")
    try:
        data = shop_admin_graphql_call(
            shop_norm,
            token,
            CURRENCY_QUERY,
            {},
            api_version,
            auth=auth,
            operation="shopEnabledCurrencies",
        )
        if data.get("errors"):
            raise RuntimeError(str(data.get("errors")))
        codes = _collect_from_graphql_payload(data)
    except Exception:
        logger.warning("shop_enabled_currencies_graphql_failed", exc_info=True)
        fb = normalize_currency_code(fallback_primary or "")
        codes = [fb] if fb and fb in ALLOWED_PRICING_CURRENCIES else []

    if not codes:
        fb = normalize_currency_code(fallback_primary or "")
        if fb and fb in ALLOWED_PRICING_CURRENCIES:
            codes = [fb]

    if not codes:
        codes = ["USD"]

    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression="SET #ec = :ec, #es = :es, #u = :u",
        ExpressionAttributeNames={
            "#ec": "shop_enabled_currencies_json",
            "#es": "shop_enabled_currencies_synced_at",
            "#u": "updated_at",
        },
        ExpressionAttributeValues={
            ":ec": json.dumps(codes, ensure_ascii=False),
            ":es": now,
            ":u": now,
        },
    )
    return codes

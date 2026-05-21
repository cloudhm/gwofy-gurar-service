"""Pull Shopify shop profile into SHOP#... METADATA."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from .markets_sync import sync_market_rates_after_profile
from .models import GSI2_PK_SHOP_INDEX, SK_METADATA, pk_shop
from .shop_enabled_currencies import sync_shop_enabled_currencies
from .shop_offline_access import ShopAdminAuth, shop_admin_graphql_call

SHOP_QUERY = """
query ShopProfile {
  shop {
    id
    name
    email
    currencyCode
    ianaTimezone
    billingAddress { countryCodeV2 }
    plan { displayName shopifyPlus }
    primaryDomain { host }
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


def sync_shop_profile(
    table,
    shop: str,
    token: str,
    api_version: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> dict[str, Any]:
    """Fetch shop from Admin GraphQL and merge into METADATA. Returns updated shop fields dict."""
    shop_norm = shop.strip().lower().rstrip("/")
    data = shop_admin_graphql_call(
        shop_norm, token, SHOP_QUERY, {}, api_version, auth=auth, operation="shopProfile"
    )
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    s = data.get("data", {}).get("shop") or {}
    now = datetime.now(timezone.utc).isoformat()

    billing = s.get("billingAddress") or {}
    plan = s.get("plan") or {}
    domain = s.get("primaryDomain") or {}

    expr_names: dict[str, str] = {
        "#u": "updated_at",
        "#cur": "shop_currency_code",
        "#sync": "shop_profile_synced_at",
        "#name": "shop_name",
        "#email": "shop_email",
        "#tz": "iana_timezone",
        "#gid": "shop_gid",
        "#dom": "primary_domain_host",
        "#plan": "shop_plan_display_name",
        "#plus": "shop_shopify_plus",
        "#bill": "billing_country_code",
    }
    expr_vals: dict[str, Any] = {
        ":u": now,
        ":cur": str(s.get("currencyCode") or ""),
        ":sync": now,
        ":name": str(s.get("name") or "")[:500],
        ":email": str(s.get("email") or "")[:500],
        ":tz": str(s.get("ianaTimezone") or "")[:100],
        ":gid": str(s.get("id") or "")[:120],
        ":dom": str(domain.get("host") or "")[:255],
        ":plan": str(plan.get("displayName") or "")[:200],
        ":plus": bool(plan.get("shopifyPlus")),
        ":bill": str(billing.get("countryCodeV2") or "")[:10],
    }

    update_parts = [
        "#u = :u",
        "#cur = :cur",
        "#sync = :sync",
        "#name = :name",
        "#email = :email",
        "#tz = :tz",
        "#gid = :gid",
        "#dom = :dom",
        "#plan = :plan",
        "#plus = :plus",
        "#bill = :bill",
    ]
    cur = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    if cur.get("installation_status") == "ACTIVE" and not cur.get("gsi2pk"):
        expr_names["#g2p"] = "gsi2pk"
        expr_names["#g2s"] = "gsi2sk"
        expr_vals[":g2p"] = GSI2_PK_SHOP_INDEX
        installed = str(cur.get("installed_at") or now)
        expr_vals[":g2s"] = f"{installed}#{shop_norm}"
        update_parts.extend(["#g2p = :g2p", "#g2s = :g2s"])

    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_vals,
    )

    try:
        sync_market_rates_after_profile(
            table,
            shop_norm,
            token,
            api_version,
            billing_country=str(billing.get("countryCodeV2") or ""),
            auth=auth,
        )
    except Exception:
        logger.warning("market_rates_sync_skipped", exc_info=True)

    try:
        sync_shop_enabled_currencies(
            table,
            shop_norm,
            token,
            api_version,
            fallback_primary=str(s.get("currencyCode") or ""),
            auth=auth,
        )
    except Exception:
        logger.warning("shop_enabled_currencies_sync_skipped", exc_info=True)

    return {
        "shop_currency_code": expr_vals[":cur"],
        "shop_profile_synced_at": now,
        "shop_name": expr_vals[":name"],
        "shop_email": expr_vals[":email"],
        "iana_timezone": expr_vals[":tz"],
        "shop_gid": expr_vals[":gid"],
        "primary_domain_host": expr_vals[":dom"],
        "shop_plan_display_name": expr_vals[":plan"],
        "shop_shopify_plus": expr_vals[":plus"],
        "billing_country_code": expr_vals[":bill"],
    }

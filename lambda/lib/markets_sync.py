"""Shopify Markets → default SP rate (4%) per country in Dynamo `sp_market_rates_json`."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .models import SK_METADATA, pk_shop
from .shipping_country_defaults import get_shipping_country_defaults, supported_country_codes
from .shopify_api import graphql_request

logger = logging.getLogger(__name__)

DEFAULT_MARKET_RATE = "0.04"

MARKETS_Q = """
query GwofyMarkets {
  markets(first: 50) {
    nodes {
      id
      name
      regions(first: 50) {
        nodes {
          __typename
          ... on MarketRegionCountry {
            code
          }
        }
      }
    }
  }
}
"""


def _country_codes_from_markets_payload(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    root = data.get("data") or {}
    conn = root.get("markets") or {}
    for node in conn.get("nodes") or []:
        regions = (node.get("regions") or {}).get("nodes") or []
        for reg in regions:
            if reg.get("__typename") == "MarketRegionCountry":
                code = str(reg.get("code") or "").strip().upper()
                if code and code not in out:
                    out.append(code)
    return out


def fetch_market_country_codes(shop: str, token: str, api_version: str) -> list[str]:
    shop = shop.strip().lower().rstrip("/")
    data = graphql_request(shop, token, MARKETS_Q, {}, api_version=api_version)
    if data.get("errors"):
        logger.warning("markets_query_errors", extra={"errors": str(data["errors"])[:500]})
        return []
    return _country_codes_from_markets_payload(data)


def merge_market_rates_json(
    existing_json: str | None,
    country_codes: list[str],
    *,
    default_rate: str = DEFAULT_MARKET_RATE,
    allowed_countries: set[str] | None = None,
    default_rates_by_country: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """Return (json_str, changed). New keys use default_rates_by_country[cc] or default_rate."""
    try:
        rates = json.loads(existing_json or "{}")
    except json.JSONDecodeError:
        rates = {}
    if not isinstance(rates, dict):
        rates = {}
    changed = False
    for cc in country_codes:
        key = str(cc).upper()
        if allowed_countries is not None and key not in allowed_countries:
            continue
        if key not in rates:
            dr = default_rate
            if default_rates_by_country and key in default_rates_by_country:
                dr = str(default_rates_by_country[key]).strip() or default_rate
            rates[key] = dr
            changed = True
    return json.dumps(rates, ensure_ascii=False, sort_keys=True), changed


def prune_market_rates_to_countries(existing_json: str | None, keep_country_codes: set[str]) -> tuple[str, bool]:
    """Remove rate entries for countries no longer present in Shopify Markets (+ billing)."""
    try:
        rates = json.loads(existing_json or "{}")
    except json.JSONDecodeError:
        rates = {}
    if not isinstance(rates, dict):
        rates = {}
    keep = {str(c).upper() for c in keep_country_codes}
    changed = False
    keys = list(rates.keys())
    for k in keys:
        ku = str(k).upper()
        if ku not in keep:
            del rates[k]
            changed = True
    return json.dumps(rates, ensure_ascii=False, sort_keys=True), changed


def sync_market_rates_after_profile(
    table,
    shop: str,
    token: str,
    api_version: str,
    *,
    billing_country: str | None = None,
) -> None:
    """After shop profile row exists: merge 4% defaults for Markets countries + billing country."""
    shop_norm = shop.strip().lower().rstrip("/")
    meta = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    existing = meta.get("sp_market_rates_json")
    countries = fetch_market_country_codes(shop_norm, token, api_version)
    bc = (billing_country or meta.get("billing_country_code") or "").strip().upper()
    if bc and bc not in countries:
        countries = [bc, *countries]
    allowed = supported_country_codes(table)
    gdefs = get_shipping_country_defaults(table)
    rate_by_cc = {k: str(v.get("rate", DEFAULT_MARKET_RATE)).strip() or DEFAULT_MARKET_RATE for k, v in gdefs.items()}
    merged, changed = merge_market_rates_json(
        existing if isinstance(existing, str) else None,
        countries,
        allowed_countries=allowed,
        default_rates_by_country=rate_by_cc,
    )
    keep_set = {str(c).upper() for c in countries if not allowed or str(c).upper() in allowed}
    pruned_change = False
    if keep_set:
        merged, pruned_change = prune_market_rates_to_countries(merged, keep_set)
    changed = changed or pruned_change
    max_cov = meta.get("sp_max_coverage_usd")
    need_max = max_cov is None
    if not changed and not need_max:
        return
    expr_parts = ["#u = :u"]
    names: dict[str, str] = {"#u": "updated_at"}
    vals: dict[str, Any] = {":u": datetime.now(timezone.utc).isoformat()}
    if changed:
        names["#mr"] = "sp_market_rates_json"
        vals[":mr"] = merged
        expr_parts.append("#mr = :mr")
    if need_max:
        names["#mx"] = "sp_max_coverage_usd"
        vals[":mx"] = Decimal("9000")
        expr_parts.append("#mx = :mx")
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )

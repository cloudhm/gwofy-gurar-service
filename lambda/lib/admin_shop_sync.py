"""Admin-triggered Shopify → Dynamo sync by resource type."""

from __future__ import annotations

import logging
from typing import Any

from .customer_order_sync import sync_orders
from .markets_sync import sync_market_rates_after_profile
from .models import SK_METADATA, pk_shop
from .product_sync import sync_products_initial
from .shop_enabled_currencies import sync_shop_enabled_currencies
from .shop_profile_sync import sync_shop_profile

logger = logging.getLogger(__name__)

RESOURCE_ALIASES: dict[str, str] = {
    "profile": "shop_profile",
    "shop": "shop_profile",
    "shop_profile": "shop_profile",
    "products": "products",
    "product": "products",
    "orders": "orders",
    "order": "orders",
    "currencies": "currencies",
    "currency": "currencies",
    "markets": "markets",
    "market": "markets",
    "catalog": "catalog",
    "all": "all",
}

ALL_RESOURCES = ("shop_profile", "products", "orders", "currencies", "markets")


def normalize_resources(raw: list[str] | None) -> list[str]:
    """Expand aliases; dedupe preserving order."""
    if not raw:
        return ["all"]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = RESOURCE_ALIASES.get(str(item).strip().lower())
        if not key:
            raise ValueError(f"unknown_resource:{item}")
        if key == "all":
            for r in ALL_RESOURCES:
                if r not in seen:
                    seen.add(r)
                    out.append(r)
            continue
        if key == "catalog":
            for r in ("products", "orders"):
                if r not in seen:
                    seen.add(r)
                    out.append(r)
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _clear_sync_checkpoint(table, shop: str, sk: str) -> None:
    shop_norm = shop.strip().lower().rstrip("/")
    table.put_item(
        Item={
            "pk": f"SYNC#{shop_norm}",
            "sk": sk,
            "graphql_page_cursor": None,
            "updated_at": None,
        }
    )


def run_admin_shop_sync(
    table,
    shop: str,
    store_number: str,
    token: str,
    kms_key_id: str,
    api_version: str,
    resources: list[str],
    *,
    reset_checkpoints: bool = False,
) -> dict[str, Any]:
    """
    Run requested sync steps for one ACTIVE shop. Returns per-resource status map.
    Raises on token/shop errors; individual resource failures are captured in results.
    """
    shop_norm = shop.strip().lower().rstrip("/")
    meta = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    if meta.get("installation_status") != "ACTIVE":
        raise ValueError("shop_not_active")
    if not meta.get("access_token_enc"):
        raise ValueError("missing_access_token")

    normalized = normalize_resources(resources)
    results: dict[str, Any] = {"shop": shop_norm, "resources": normalized, "steps": {}}
    protection_gid = meta.get("protection_product_gid")

    for res in normalized:
        try:
            if res == "shop_profile":
                sync_shop_profile(table, shop_norm, token, api_version)
                results["steps"][res] = {"ok": True}
            elif res == "currencies":
                fb = str(meta.get("shop_currency_code") or "").strip().upper()
                codes = sync_shop_enabled_currencies(
                    table, shop_norm, token, api_version, fallback_primary=fb or None
                )
                results["steps"][res] = {"ok": True, "currencies": codes}
            elif res == "markets":
                sync_market_rates_after_profile(
                    table,
                    shop_norm,
                    token,
                    api_version,
                    billing_country=str(meta.get("billing_country_code") or ""),
                )
                results["steps"][res] = {"ok": True}
            elif res == "products":
                if reset_checkpoints:
                    _clear_sync_checkpoint(table, shop_norm, "PRODUCTS#CHECKPOINT")
                sync_products_initial(table, shop_norm, store_number, token, api_version)
                results["steps"][res] = {"ok": True}
            elif res == "orders":
                if reset_checkpoints:
                    _clear_sync_checkpoint(table, shop_norm, "ORDERS#CHECKPOINT")
                meta = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
                protection_gid = meta.get("protection_product_gid")
                sync_orders(
                    table,
                    shop_norm,
                    store_number,
                    token,
                    api_version,
                    protection_product_gid=protection_gid,
                )
                results["steps"][res] = {"ok": True}
            else:
                results["steps"][res] = {"ok": False, "error": "unsupported"}
        except Exception as e:
            logger.exception("admin_shop_sync_step_failed", extra={"shop": shop_norm, "resource": res})
            results["steps"][res] = {"ok": False, "error": str(e)[:500]}

    failed = [k for k, v in results["steps"].items() if not v.get("ok")]
    results["ok"] = len(failed) == 0
    if failed:
        results["failed_resources"] = failed
    return results

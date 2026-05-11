"""Worker: ACTIVATE_APP — FX, protection product upsert, METADATA update."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .audit import append_audit
from .fx_rates import fetch_usd_to_currency
from .models import SK_METADATA, pk_shop
from .default_protection_tiers import PROTECTION_PRODUCT_HANDLE
from .pricing_config import get_pricing_model
from .protection_product import upsert_protection_product
from .shop_profile_sync import sync_shop_profile

DEFAULT_TITLE = "Shipping Protection"
DEFAULT_VENDOR = "xcotton"
DEFAULT_TYPE = "shipping-protection"


def run_activate_app(
    table,
    shop: str,
    store_number: str,
    token: str,
    kms_key_id: str,
    api_version: str,
) -> None:
    shop_norm = shop.strip().lower().rstrip("/")
    sync_shop_profile(table, shop_norm, token, api_version)
    meta = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    currency = str(meta.get("shop_currency_code") or "USD").upper()
    tiers_cfg = get_pricing_model(table)
    rate, fx_date = fetch_usd_to_currency(currency)
    tiers_shop: list[tuple[str, Decimal, str]] = []
    for t in tiers_cfg:
        code = str(t.get("plan_code") or "")
        sku = str(t.get("sku") or code)
        price_usd = Decimal(str(t.get("price_usd", 0)))
        price_shop = (price_usd * Decimal(str(rate))).quantize(Decimal("0.01"))
        tiers_shop.append((code, price_shop, sku))

    existing = meta.get("protection_product_gid")
    pid = upsert_protection_product(
        shop_norm,
        token,
        api_version,
        existing_product_gid=str(existing) if existing else None,
        tiers_shop=tiers_shop,
        title=DEFAULT_TITLE,
        vendor=DEFAULT_VENDOR,
        product_type=DEFAULT_TYPE,
        handle=PROTECTION_PRODUCT_HANDLE,
    )
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression=(
            "SET activation_status = :a, protection_product_gid = :p, "
            "last_fx_usd_to_shop = :r, last_fx_as_of = :fxd, last_fx_target_ccy = :ccy, updated_at = :u"
        ),
        ExpressionAttributeValues={
            ":a": "ACTIVATED",
            ":p": pid,
            ":r": str(rate),
            ":fxd": fx_date or now,
            ":ccy": currency,
            ":u": now,
        },
    )
    append_audit(
        table,
        shop_norm,
        actor_type="system",
        actor_id="worker",
        action="ACTIVATE_SUCCESS",
        outcome="ok",
        resource="protection_product",
        detail={"product_gid": pid, "fx_date": fx_date, "currency": currency},
    )


def run_activate_app_safe(
    table,
    shop: str,
    store_number: str,
    token: str,
    kms_key_id: str,
    api_version: str,
) -> None:
    try:
        run_activate_app(table, shop, store_number, token, kms_key_id, api_version)
    except Exception as e:
        shop_norm = shop.strip().lower().rstrip("/")
        append_audit(
            table,
            shop_norm,
            actor_type="system",
            actor_id="worker",
            action="ACTIVATE_FAIL",
            outcome="error",
            resource="protection_product",
            detail={"error": str(e)[:2000]},
        )
        raise

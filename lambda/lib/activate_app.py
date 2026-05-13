"""Synchronous activation: native per-currency tier prices from admin config (no FX)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .audit import append_audit
from .models import SK_METADATA, pk_shop
from .default_protection_tiers import PROTECTION_PRODUCT_HANDLE, PROTECTION_PRODUCT_VENDOR
from .pricing_config import (
    get_pricing_rows,
    get_supported_currencies,
    tier_native_amount,
)
from .protection_product import upsert_protection_product

DEFAULT_TITLE = "Shipping Protection"
DEFAULT_TYPE = "shipping-protection"


class ActivateAppError(Exception):
    """Business-rule failure surfaced to POST /api/activate as 4xx."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        currency: str | None = None,
        supported: list[str] | None = None,
    ):
        self.code = code
        self.message = message
        self.currency = currency
        self.supported = supported
        super().__init__(message)


def _persist_last_activation_error(table, shop_norm: str, doc: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression="SET last_activation_error = :e, updated_at = :u",
        ExpressionAttributeValues={
            ":e": json.dumps(doc, ensure_ascii=False, default=str)[:35000],
            ":u": now,
        },
    )


def run_activate_app(
    table,
    shop: str,
    store_number: str,
    token: str,
    kms_key_id: str,
    api_version: str,
    *,
    actor_sub: str = "",
) -> None:
    shop_norm = shop.strip().lower().rstrip("/")
    meta = table.get_item(Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA}).get("Item") or {}
    currency_raw = str(meta.get("shop_currency_code") or "").strip()
    if not currency_raw:
        raise ActivateAppError(
            "shop_profile_not_ready",
            "Shop currency is not available yet; wait for install sync or webhooks, then retry.",
        )
    currency = currency_raw.upper()
    supported = get_supported_currencies(table)
    if currency not in supported:
        raise ActivateAppError(
            "currency_not_supported",
            f"Shop currency {currency} is not enabled for pricing.",
            currency=currency,
            supported=list(supported),
        )
    tiers_cfg = get_pricing_rows(table, currency)
    if not tiers_cfg:
        raise ActivateAppError(
            "pricing_not_configured",
            f"No pricing tiers configured for {currency}.",
            currency=currency,
            supported=list(supported),
        )

    tiers_shop: list[tuple[str, Decimal, str]] = []
    for t in tiers_cfg:
        code = str(t.get("plan_code") or t.get("sku") or "")
        sku = code
        amt = tier_native_amount(t) if isinstance(t, dict) else None
        if amt is None:
            raise ActivateAppError(
                "pricing_not_configured",
                f"Tier {code!r} missing native price for {currency}.",
                currency=currency,
            )
        price_shop = Decimal(str(amt)).quantize(Decimal("0.01"))
        tiers_shop.append((code, price_shop, sku))

    existing = meta.get("protection_product_gid")
    pid = upsert_protection_product(
        shop_norm,
        token,
        api_version,
        existing_product_gid=str(existing) if existing else None,
        tiers_shop=tiers_shop,
        title=DEFAULT_TITLE,
        vendor=PROTECTION_PRODUCT_VENDOR,
        product_type=DEFAULT_TYPE,
        handle=PROTECTION_PRODUCT_HANDLE,
    )
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"pk": pk_shop(shop_norm), "sk": SK_METADATA},
        UpdateExpression=(
            "SET activation_status = :a, protection_product_gid = :p, updated_at = :u "
            "REMOVE last_activation_error, last_fx_usd_to_shop, last_fx_as_of, last_fx_target_ccy"
        ),
        ExpressionAttributeValues={
            ":a": "ACTIVATED",
            ":p": pid,
            ":u": now,
        },
    )
    append_audit(
        table,
        shop_norm,
        actor_type="merchant",
        actor_id=actor_sub or "activate",
        action="ACTIVATE_SUCCESS",
        outcome="ok",
        resource="protection_product",
        detail={"product_gid": pid, "currency": currency},
    )


def run_activate_app_safe(
    table,
    shop: str,
    store_number: str,
    token: str,
    kms_key_id: str,
    api_version: str,
    *,
    actor_sub: str = "",
) -> None:
    shop_norm = shop.strip().lower().rstrip("/")
    try:
        run_activate_app(
            table,
            shop,
            store_number,
            token,
            kms_key_id,
            api_version,
            actor_sub=actor_sub,
        )
    except ActivateAppError as e:
        now = datetime.now(timezone.utc).isoformat()
        doc: dict[str, Any] = {"code": e.code, "message": e.message, "at": now}
        if e.currency:
            doc["currency"] = e.currency
        if e.supported is not None:
            doc["supported"] = e.supported
        _persist_last_activation_error(table, shop_norm, doc)
        append_audit(
            table,
            shop_norm,
            actor_type="merchant",
            actor_id=actor_sub or "activate",
            action="ACTIVATE_FAIL",
            outcome="error",
            resource="protection_product",
            detail={"code": e.code, "message": e.message[:2000]},
        )
        raise
    except Exception as e:
        now = datetime.now(timezone.utc).isoformat()
        _persist_last_activation_error(
            table,
            shop_norm,
            {
                "code": "activate_failed",
                "message": str(e)[:2000],
                "at": now,
            },
        )
        append_audit(
            table,
            shop_norm,
            actor_type="merchant",
            actor_id=actor_sub or "activate",
            action="ACTIVATE_FAIL",
            outcome="error",
            resource="protection_product",
            detail={"error": str(e)[:2000]},
        )
        raise

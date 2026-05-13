"""Storefront cart bootstrap JSON (HMAC `POST /api/cart-config`)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .activity_config import get_activity_info
from .calc_coverage_tips_config import effective_calc_coverage_tips
from .tips_config import get_tips_info
from .default_protection_tiers import PROTECTION_PRODUCT_HANDLE
from .pricing_config import get_pricing_model, get_supported_currencies
from .max_coverage_config import effective_max_coverage_for_cart
from .merchant_premium_rules import parse_rules_from_meta
from .shipping_country_defaults import effective_rate


def stable_shop_plugin_id(store_number: str) -> int:
    if not store_number:
        return 0
    if store_number.isdigit():
        return int(store_number) % 2_147_483_647
    h = hashlib.sha256(store_number.encode()).hexdigest()
    return int(h[:12], 16) % 2_147_483_647


def build_cart_plugin_response(
    table,
    meta: dict[str, Any],
    *,
    country: str,
    debug_mode: bool = False,
) -> dict[str, Any]:
    """Shape aligned with legacy Gwofy-style cart plugin; extend via admin/Dynamo later."""
    sp_st = str(meta.get("shipping_protection_status") or "CLOSED")
    ri_st = str(meta.get("return_insurance_status") or "CLOSED")
    sp_open = sp_st.startswith("OPEN")
    ri_open = ri_st.startswith("OPEN")

    cc = (country or "").strip().upper()
    shop_ccy = str(meta.get("shop_currency_code") or "").strip().upper()
    max_cov, max_cov_ccy = effective_max_coverage_for_cart(table, meta, shop_ccy or None)
    max_cov = float(max_cov)
    rate_s = effective_rate(table, meta, cc)
    try:
        rate_f = float(rate_s)
    except ValueError:
        rate_f = 0.04
        rate_s = "0.04"

    tiers = get_pricing_model(table, shop_ccy) if shop_ccy else []
    if not tiers:
        tiers = [{"plan_code": "S0001", "price": 0.98}]
    first = tiers[0] if tiers else {}
    sp_sku = str(first.get("plan_code") or first.get("sku") or "S0001")
    sn = str(meta.get("store_number") or "")
    pub = str(meta.get("updated_at") or datetime.now(timezone.utc).isoformat())

    # Single Shopify protection product; SP vs PP in `auth` are feature flags only.
    protection_product = {
        "handle": PROTECTION_PRODUCT_HANDLE,
        "publishAt": pub,
        "sku": sp_sku,
    }
    activity = get_activity_info(table)
    tips = get_tips_info(table)
    calc_tips = effective_calc_coverage_tips(table, meta)
    premium_rules, _premium_warn = parse_rules_from_meta(table, meta)

    return {
        "auth": {
            "isOpenForSp": sp_open,
            "isOpenForPp": ri_open,
            "isShowCartPPButton": ri_open,
            "isCartDefaultOpen": sp_open,
            "isDebugMode": debug_mode,
        },
        "versions": {
            "commonVersion": "v1-0-5",
            "commonDebugVersion": "",
            "customVersion": "",
            "customDebugVersion": "",
            "calcVersion": "v1-0-3",
            "calcDebugVersion": "",
        },
        "dataInfo": {
            "activityInfo": activity,
            "productInfo": {
                "protectionProduct": dict(protection_product),
                "spProduct": dict(protection_product),
                "ppProduct": dict(protection_product),
            },
            "shop": {"shopId": stable_shop_plugin_id(sn)},
        },
        "tipsInfo": tips,
        "xmhSupportCurrency": get_supported_currencies(table),
        "xmhSupportLocale": [
            {
                "languageISO": "en",
                "languageName": "English",
                "languageExtISO": "en-US",
                "languageZHName": "英语（美国）",
            },
            {
                "languageISO": "zh",
                "languageName": "中文（简体）",
                "languageExtISO": "zh-CN",
                "languageZHName": "简体中文",
            },
        ],
        "calcInfo": {
            "calcScope": "client",
            "maxAmount": max_cov,
            "maxAmountCurrency": max_cov_ccy,
            "originRate": "",
            "reportUrl": "",
            "spBelowMinCoverageTip": calc_tips["spBelowMinCoverageTip"],
            "spGreaterMaxCoverageTip": calc_tips["spGreaterMaxCoverageTip"],
            "spRate": rate_s,
        },
        "merchantPremiumRules": premium_rules,
        "configuration": {
            "calcVersion": "v1-0-3",
            "commonVersion": "v1-0-5",
            "currentVersion": "gwofy-cart-v1",
            "customVersion": "",
            "adapterConfig": {
                "canBePerformanceObserver": False,
                "cartRefreshMode": "update.fn.default",
                "drawerCartRefreshMode": "event:cart:refresh",
                "isForceRefreshUI": False,
                "useWebWorkerFetch": True,
            },
            "spConfig": {
                "panelConfig": {
                    "rate": rate_f,
                    "version": "checkoutPlusV3-W",
                    "onlyPrice": True,
                    "hidePrice": False,
                }
            },
            "ppConfig": {"dialogConfig": {"dialogVersion": "3.0"}},
            "spMode": "checkoutPlus",
            "spLocale": {
                "en": {
                    "mainCartTitle": "<p>Shipping Protection</p>",
                    "coverBtnText": "Cover My Order Now",
                }
            },
        },
    }

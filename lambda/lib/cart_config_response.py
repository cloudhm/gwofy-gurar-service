"""Storefront cart bootstrap JSON (HMAC `POST /api/cart-config`)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .default_protection_tiers import PROTECTION_PRODUCT_HANDLE
from .pricing_config import get_pricing_model
from .shipping_country_defaults import effective_max_coverage_usd, effective_rate


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
    currency: str,
    language: str,
    debug_mode: bool = False,
) -> dict[str, Any]:
    """Shape aligned with legacy Xcotton-style cart plugin; extend via admin/Dynamo later."""
    sp_st = str(meta.get("shipping_protection_status") or "CLOSED")
    ri_st = str(meta.get("return_insurance_status") or "CLOSED")
    sp_open = sp_st.startswith("OPEN")
    ri_open = ri_st.startswith("OPEN")

    cc = (country or "").strip().upper()
    max_cov = float(effective_max_coverage_usd(table, meta, cc))
    rate_s = effective_rate(table, meta, cc)
    try:
        rate_f = float(rate_s)
    except ValueError:
        rate_f = 0.04
        rate_s = "0.04"

    tiers = get_pricing_model(table)
    first = tiers[0] if tiers else {}
    sp_sku = str(first.get("sku") or first.get("plan_code") or "S0001")
    sn = str(meta.get("store_number") or "")
    pub = str(meta.get("updated_at") or datetime.now(timezone.utc).isoformat())

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
            "activityInfo": {"activityExtInfo": "", "activityState": 0},
            "productInfo": {
                "ppProduct": {
                    "handle": "product-protection",
                    "publishAt": pub,
                    "sku": "XMHPPSKU",
                },
                "spProduct": {
                    "handle": PROTECTION_PRODUCT_HANDLE,
                    "publishAt": pub,
                    "sku": sp_sku,
                },
            },
            "shop": {"shopId": stable_shop_plugin_id(sn)},
        },
        "tipsInfo": {
            "ppVersion": {
                "faqUrl": "https://gwofy.com/docs/pp-faq",
                "locationType": "normal",
                "popup": "gwofy",
                "terms": "https://gwofy.com/terms/pp",
            },
            "spVersion": {
                "faqUrl": "https://gwofy.com/docs/sp-faq",
                "popup": "SP-GWOFY",
                "terms": "https://gwofy.com/terms/sp",
            },
        },
        "xmhSupportCurrency": ["USD", "CAD", "EUR", "GBP", "AUD", "JPY"],
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
            "originRate": "",
            "reportUrl": "",
            "spBelowMinCoverageTip": "",
            "spGreaterMaxCoverageTip": "",
            "spMaxCoverage": max_cov,
            "spMinCoverage": 0,
            "spRate": rate_s,
            "zeroBuyConf": {
                "enable": False,
                "enableActivity": False,
                "enableContainsMatch": False,
                "enableInsure": False,
                "ignoreCoverageLimit": False,
                "itemFilterRules": None,
                "lowPriceInt": 0,
                "orderFilterRules": None,
                "rate": "",
                "skipInsuredMail": False,
            },
        },
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
                (language or "en"): {
                    "mainCartTitle": "<p>Shipping Protection</p>",
                    "coverBtnText": "Cover My Order Now",
                }
            },
        },
    }

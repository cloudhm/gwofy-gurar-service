"""storefront_gwofy_config merge and validation."""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from lib.shop_enabled_currencies import shop_supported_currencies_list
from lib.storefront_gwofy_config import (
    build_effective_gwofy_config,
    default_gwofy_config,
    derived_from_meta,
    derived_readonly_keys_in_patch,
    effective_protection_product_handle,
    ensure_default_remote_script_urls,
    merge_storefront_config_patch,
    normalize_shop_host,
    validate_storefront_config_patch,
)


def test_normalize_shop_host():
    assert normalize_shop_host("HTTPS://GWO-DEV.myshopify.com/") == "gwo-dev.myshopify.com"
    assert normalize_shop_host("gwo-dev.myshopify.com") == "gwo-dev.myshopify.com"
    assert normalize_shop_host("") is None


def test_default_has_product_handle():
    d = default_gwofy_config()
    assert d["productHandle"] == "GWOFY-SHIPPING-PROTECTION-QAQWER"
    assert d["pricing"]["calcRate"] == "0.04"
    assert "styles" not in d


@contextmanager
def _patch_derived_helpers():
    with (
        patch("lib.storefront_gwofy_config.get_tips_info", return_value={"spVersion": {}}),
        patch(
            "lib.storefront_gwofy_config.shop_supported_currencies_list",
            return_value=["USD"],
        ),
    ):
        yield


def test_derived_is_open_for_sp_only_when_open_audited():
    table = MagicMock()
    with _patch_derived_helpers():
        audited = derived_from_meta(
            table,
            {"shipping_protection_status": "OPEN_AUDITED", "billing_country_code": "US"},
            "gwo-dev.myshopify.com",
        )
        unaudited = derived_from_meta(
            table,
            {"shipping_protection_status": "OPEN_UNAUDITED", "billing_country_code": "US"},
            "gwo-dev.myshopify.com",
        )
    assert audited["auth"]["isOpenForSP"] is True
    assert unaudited["auth"]["isOpenForSP"] is False
    assert unaudited["isCartDefaultOpen"] is True


def test_derived_sets_shop_host_as_shop_id():
    meta = {
        "shipping_protection_status": "OPEN_AUDITED",
        "shop_currency_code": "USD",
        "billing_country_code": "US",
    }
    table = MagicMock()
    with (
        patch(
            "lib.storefront_gwofy_config.get_tips_info",
            return_value={"spVersion": {"faqUrl": "https://f", "terms": "https://t", "popup": "SP-X"}},
        ),
        patch(
            "lib.storefront_gwofy_config.shop_supported_currencies_list",
            return_value=["USD"],
        ),
    ):
        d = derived_from_meta(table, meta, "gwo-dev.myshopify.com")
    assert d["shopId"] == "gwo-dev.myshopify.com"
    assert d["auth"]["isOpenForSP"] is True
    assert "pricing" not in d


def test_merge_override_text():
    base = {"text": {"sp": {"title": "A", "desc": "B"}}}
    patch = {"text": {"sp": {"title": "Custom"}}}
    merged = merge_storefront_config_patch(base, patch)
    assert merged["text"]["sp"]["title"] == "Custom"
    assert merged["text"]["sp"]["desc"] == "B"


def test_effective_protection_product_handle_from_metadata():
    table = MagicMock()
    h = effective_protection_product_handle(
        table,
        {"protection_product_handle": "my-shop-sp-product"},
    )
    assert h == "my-shop-sp-product"
    table.get_item.assert_not_called()


def test_effective_protection_product_handle_from_product_mirror():
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {
            "payload": json.dumps({"handle": "mirror-handle"}),
        }
    }
    h = effective_protection_product_handle(
        table,
        {
            "store_number": "1000000001",
            "protection_product_gid": "gid://shopify/Product/99",
        },
    )
    assert h == "mirror-handle"


def test_shop_supported_currencies_from_enabled_json():
    meta = {
        "shop_enabled_currencies_json": '["USD","EUR","CAD"]',
        "shop_currency_code": "GBP",
    }
    assert shop_supported_currencies_list(meta) == ["CAD", "EUR", "USD"]


def test_shop_supported_currencies_fallback_to_shop_currency():
    meta = {"shop_currency_code": "EUR"}
    assert shop_supported_currencies_list(meta) == ["EUR"]


def test_validate_rejects_supported_currencies_patch():
    patch, err = validate_storefront_config_patch({"supportedCurrencies": ["USD"]})
    assert err == "derived_readonly_keys"


def test_validate_rejects_product_handle_patch():
    patch, err = validate_storefront_config_patch({"productHandle": "custom-handle"})
    assert err == "derived_readonly_keys"
    assert "productHandle" in derived_readonly_keys_in_patch({"productHandle": "custom-handle"})


def test_validate_accepts_pricing_calc_rate():
    patch, err = validate_storefront_config_patch({"pricing": {"calcRate": "0.05"}})
    assert err is None
    assert patch["pricing"]["calcRate"] == "0.05"


def test_validate_allows_sp_min_coverage():
    patch, err = validate_storefront_config_patch({"pricing": {"spMinCoverage": "10"}})
    assert err is None
    assert patch["pricing"]["spMinCoverage"] == "10"


def test_effective_pricing_from_defaults_and_override():
    meta = {"storefront_config_json": '{"pricing":{"calcRate":"0.05","spMaxCoverage":"8000"}}'}
    table = MagicMock()
    with _patch_derived_helpers():
        eff = build_effective_gwofy_config(table, meta, "gwo-dev.myshopify.com")
    assert eff["pricing"]["calcRate"] == "0.05"
    assert eff["pricing"]["spMaxCoverage"] == "8000"
    assert eff["pricing"]["hardMaxAmount"] == "13000"


def test_ensure_default_remote_script_urls(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://sp-prod.gwofy.com")
    cfg = ensure_default_remote_script_urls({"remoteScriptUrls": []})
    assert cfg["remoteScriptUrls"] == [
        "https://sp-prod.gwofy.com/static/app-storefront.js?v=1.0.0"
    ]
    again = ensure_default_remote_script_urls(cfg)
    assert again["remoteScriptUrls"] == cfg["remoteScriptUrls"]


def test_build_effective_includes_default_storefront_url(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://sp-prod.gwofy.com")
    meta = {"shipping_protection_status": "CLOSED", "shop_currency_code": "USD"}
    table = MagicMock()
    with _patch_derived_helpers():
        eff = build_effective_gwofy_config(table, meta, "gwo-dev.myshopify.com")
    assert eff["remoteScriptUrls"][0] == (
        "https://sp-prod.gwofy.com/static/app-storefront.js?v=1.0.0"
    )


def test_build_effective_merges_layers():
    meta = {
        "shipping_protection_status": "CLOSED",
        "shop_currency_code": "USD",
        "billing_country_code": "US",
        "storefront_config_json": '{"text":{"sp":{"title":"Shop Title"}}}',
    }
    table = MagicMock()
    with _patch_derived_helpers():
        eff = build_effective_gwofy_config(table, meta, "gwo-dev.myshopify.com")
    assert eff["text"]["sp"]["title"] == "Shop Title"
    assert eff["shopId"] == "gwo-dev.myshopify.com"
    assert eff["auth"]["isOpenForSP"] is False

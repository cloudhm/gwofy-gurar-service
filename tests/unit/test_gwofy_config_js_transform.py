"""gwofy_config_js_transform — literal g.GWOFY_CONFIG upload and serve injection."""

from lib.gwofy_config_js_transform import (
    inject_gwofy_config_into_template,
    parse_script_config_overlay,
    validate_gwofy_config_assignment,
)
from lib.storefront_gwofy_config import build_effective_gwofy_config


LITERAL_TEMPLATE = """
(function (g) {
  function buildDefaultGwofyStyles() { return { widget: "base" }; }
  g.GWOFY_CONFIG = {
    debug: true, // config
    auth: { isOpenForSP: true },
    productHandle: "evil-handle",
    pricing: { calcRate: "0.01" },
    text: { sp: { title: "From Template" } },
    styles: buildDefaultGwofyStyles(),
  };
})(window);
"""


def test_validate_literal_assignment_without_marker():
    validate_gwofy_config_assignment(LITERAL_TEMPLATE)


def test_parse_overlay_from_literal():
    overlay = parse_script_config_overlay(LITERAL_TEMPLATE)
    assert overlay["debug"] is True
    assert overlay["pricing"]["calcRate"] == "0.01"
    assert overlay["text"]["sp"]["title"] == "From Template"
    assert "styles" not in overlay


def test_inject_replaces_literal_assignment():
    merged = {"debug": False, "pricing": {"calcRate": "0.05"}, "shopId": "shop.myshopify.com"}
    out = inject_gwofy_config_into_template(LITERAL_TEMPLATE, merged)
    assert "g.GWOFY_CONFIG" in out
    assert "/*__GWOFY_CONFIG_JSON__*/" not in out
    assert '"calcRate": "0.05"' in out
    assert "evil-handle" not in out
    assert "buildDefaultGwofyStyles()" in out
    assert "From Template" not in out


def test_admin_override_wins_over_template_overlay():
    from unittest.mock import MagicMock, patch

    meta = {
        "shipping_protection_status": "OPEN_AUDITED",
        "shop_currency_code": "USD",
        "billing_country_code": "US",
        "storefront_config_json": '{"pricing":{"calcRate":"0.06"},"text":{"sp":{"title":"Admin Title"}}}',
    }
    table = MagicMock()
    overlay = parse_script_config_overlay(LITERAL_TEMPLATE)
    with (
        patch("lib.storefront_gwofy_config.get_tips_info", return_value={"spVersion": {}}),
        patch(
            "lib.storefront_gwofy_config.shop_supported_currencies_list",
            return_value=["USD"],
        ),
    ):
        eff = build_effective_gwofy_config(
            table,
            meta,
            "gwo-dev.myshopify.com",
            script_overlay=overlay,
        )
    assert eff["pricing"]["calcRate"] == "0.06"
    assert eff["text"]["sp"]["title"] == "Admin Title"
    assert eff["shopId"] == "gwo-dev.myshopify.com"
    assert eff["auth"]["isOpenForSP"] is True
    assert eff["productHandle"] == "GWOFY-SHIPPING-PROTECTION-QAQWER"


def test_marker_template_still_works():
    tpl = "g.GWOFY_CONFIG = Object.assign({}, /*__GWOFY_CONFIG_JSON__*/);"
    out = inject_gwofy_config_into_template(tpl, {"debug": True})
    assert '"debug": true' in out
    assert "/*__GWOFY_CONFIG_JSON__*/" not in out

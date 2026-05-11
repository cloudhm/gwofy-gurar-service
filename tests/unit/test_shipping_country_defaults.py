from lib.shipping_country_defaults import (
    effective_max_coverage_usd,
    effective_rate,
    validate_countries_payload,
)
from lib.markets_sync import merge_market_rates_json as merge_rates


def test_validate_countries_ok():
    assert validate_countries_payload({"US": {"rate": "0.04", "max_coverage_usd": 9000}}) is None


def test_validate_empty_allowed():
    assert validate_countries_payload({}) is None


def test_validate_bad_rate():
    err = validate_countries_payload({"US": {"rate": "x", "max_coverage_usd": 1}})
    assert err and "rate" in err


def test_effective_rate_shop_overrides_global(monkeypatch):
    meta = {"sp_market_rates_json": '{"US": "0.09"}'}
    fake = {"US": {"rate": "0.04", "max_coverage_usd": 9000}, "CA": {"rate": "0.05", "max_coverage_usd": 8000}}
    monkeypatch.setattr(
        "lib.shipping_country_defaults.get_shipping_country_defaults",
        lambda _t: fake,
    )
    assert effective_rate(None, meta, "US") == "0.09"
    assert effective_rate(None, meta, "CA") == "0.05"


def test_effective_max_shop_country_override(monkeypatch):
    meta = {
        "sp_country_max_overrides_json": '{"US": "12000"}',
    }
    fake = {"US": {"rate": "0.04", "max_coverage_usd": 9000}, "CA": {"rate": "0.04", "max_coverage_usd": 7000}}
    monkeypatch.setattr(
        "lib.shipping_country_defaults.get_shipping_country_defaults",
        lambda _t: fake,
    )
    assert effective_max_coverage_usd(None, meta, "US") == 12000.0
    assert effective_max_coverage_usd(None, meta, "CA") == 7000.0


def test_merge_rates_allowlist():
    j, changed = merge_rates("{}", ["US", "XX"], allowed_countries={"US"})
    assert changed is True
    import json

    d = json.loads(j)
    assert d == {"US": "0.04"}
    assert "XX" not in d

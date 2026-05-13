from lib.markets_sync import merge_market_rates_json as merge_rates
from lib.shipping_country_defaults import effective_rate, validate_countries_payload


def test_validate_countries_ok():
    assert validate_countries_payload({"US": {"rate": "0.04"}}) is None


def test_validate_empty_allowed():
    assert validate_countries_payload({}) is None


def test_validate_bad_rate():
    err = validate_countries_payload({"US": {"rate": "x"}})
    assert err and "rate" in err


def test_validate_requires_rate():
    err = validate_countries_payload({"US": {"max_coverage_usd": 9000}})
    assert err and "rate" in err


def test_effective_rate_shop_overrides_global(monkeypatch):
    meta = {"sp_market_rates_json": '{"US": "0.09"}'}
    fake = {"US": {"rate": "0.04"}, "CA": {"rate": "0.05"}}
    monkeypatch.setattr(
        "lib.shipping_country_defaults.get_shipping_country_defaults",
        lambda _t: fake,
    )
    assert effective_rate(None, meta, "US") == "0.09"
    assert effective_rate(None, meta, "CA") == "0.05"


def test_merge_rates_allowlist():
    j, changed = merge_rates("{}", ["US", "XX"], allowed_countries={"US"})
    assert changed is True
    import json

    d = json.loads(j)
    assert d == {"US": "0.04"}
    assert "XX" not in d

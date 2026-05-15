from lib.markets_sync import merge_market_rates_json


def test_merge_adds_default_rate_for_new_countries():
    j, changed = merge_market_rates_json('{"US": "0.05"}', ["US", "CA"])
    assert changed is True
    assert '"CA": "0.04"' in j
    assert '"US": "0.05"' in j


def test_merge_idempotent():
    j1, c1 = merge_market_rates_json(None, ["DE"])
    assert c1 is True
    j2, c2 = merge_market_rates_json(j1, ["DE"])
    assert c2 is False


def test_prune_removes_absent_countries():
    from lib.markets_sync import prune_market_rates_to_countries

    j, changed = prune_market_rates_to_countries('{"US": "0.04", "CA": "0.05"}', {"US"})
    assert changed is True
    assert "US" in j
    assert "CA" not in j


def test_prune_idempotent():
    from lib.markets_sync import prune_market_rates_to_countries

    j, c = prune_market_rates_to_countries('{"US": "0.04"}', {"US"})
    assert c is False

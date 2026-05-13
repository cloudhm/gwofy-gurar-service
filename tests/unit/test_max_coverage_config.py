"""Tests for currency-keyed max coverage (global + shop merge)."""

from __future__ import annotations

from decimal import Decimal

from lib.max_coverage_config import (
    effective_max_coverage_for_cart,
    effective_max_coverage_usd,
    merged_max_coverage_by_currency,
    validate_shop_max_coverage_by_currency,
)
from lib.models import PK_GLOBAL_CONFIG, SK_MAX_COVERAGE_BY_CURRENCY


def _fake_table_global(amounts: dict | None):
    import json

    class T:
        def get_item(self, Key):  # noqa: N802
            if Key.get("pk") == PK_GLOBAL_CONFIG and Key.get("sk") == SK_MAX_COVERAGE_BY_CURRENCY:
                if amounts is None:
                    return {}
                return {"Item": {"amounts_json": json.dumps(amounts)}}
            return {}

    return T()


def test_merged_shop_overrides_global():
    table = _fake_table_global({"USD": 8000.0, "EUR": 7000.0})
    meta = {"sp_max_coverage_by_currency_json": '{"USD": 12000, "JPY": 1000000}'}
    m = merged_max_coverage_by_currency(table, meta)
    assert m["USD"] == 12000.0
    assert m["EUR"] == 7000.0
    assert m["JPY"] == 1000000.0


def test_effective_max_for_cart_prefers_shop_currency():
    table = _fake_table_global({"USD": 9000, "EUR": 8000})
    meta: dict = {}
    amt, ccy = effective_max_coverage_for_cart(table, meta, "EUR")
    assert ccy == "EUR"
    assert amt == 8000.0


def test_effective_max_for_cart_fallback_usd():
    table = _fake_table_global({"USD": 5000})
    meta: dict = {}
    amt, ccy = effective_max_coverage_for_cart(table, meta, "JPY")
    assert ccy == "USD"
    assert amt == 5000.0


def test_effective_max_for_cart_legacy_meta(monkeypatch):
    monkeypatch.setattr(
        "lib.max_coverage_config.get_global_max_coverage_by_currency",
        lambda _t: {},
    )
    table = object()
    meta = {"sp_max_coverage_usd": Decimal("4500")}
    amt, ccy = effective_max_coverage_for_cart(table, meta, "GBP")
    assert ccy == "USD"
    assert amt == 4500.0


def test_effective_max_coverage_usd():
    table = _fake_table_global({"USD": 3333})
    assert effective_max_coverage_usd(table, {}) == 3333.0


def test_validate_shop_max_keys_must_be_enabled():
    allowed = frozenset({"USD", "EUR"})
    assert validate_shop_max_coverage_by_currency({"USD": 1, "EUR": 2}, allowed) is None
    err = validate_shop_max_coverage_by_currency({"USD": 1, "JPY": 2}, allowed)
    assert err and "JPY" in err


def test_validate_shop_max_requires_sync_when_empty_allowed():
    err = validate_shop_max_coverage_by_currency({"USD": 1}, frozenset())
    assert err == "shop_enabled_currencies_not_synced"

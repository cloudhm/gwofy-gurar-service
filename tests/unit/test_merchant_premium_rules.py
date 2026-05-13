import json

import pytest

from lib.merchant_premium_rules import (
    default_rules,
    normalize_for_storage,
    normalize_rules_dict,
    parse_rules_from_meta,
    validate_rules,
)
from lib.models import MERCHANT_PREMIUM_RULES_JSON


def test_meta_key_constant():
    assert MERCHANT_PREMIUM_RULES_JSON == "merchant_premium_rules_json"


@pytest.fixture
def table_us_ca(monkeypatch):
    monkeypatch.setattr(
        "lib.merchant_premium_rules.is_country_supported",
        lambda _t, cc: cc in {"US", "CA"},
    )
    return object()


def test_validate_ok_minimal(table_us_ca):
    body = {
        "version": 1,
        "markup": {"default": {"addPercent": 0, "addFixed": 0}},
        "promotions": [],
    }
    assert validate_rules(table_us_ca, body) is None


def test_validate_markup_by_country(table_us_ca):
    body = {
        "version": 1,
        "markup": {
            "default": {"addPercent": 5, "addFixed": 1},
            "byCountry": {"US": {"addPercent": 10, "addFixed": 0.5}},
        },
        "promotions": [],
    }
    assert validate_rules(table_us_ca, body) is None


def test_validate_rejects_unknown_country_markup(table_us_ca):
    body = {
        "version": 1,
        "markup": {
            "default": {"addPercent": 0, "addFixed": 0},
            "byCountry": {"XX": {"addPercent": 1, "addFixed": 0}},
        },
        "promotions": [],
    }
    assert validate_rules(table_us_ca, body) == "unsupported_country_in_markup:XX"


def test_validate_promotion_percent(table_us_ca):
    body = {
        "version": 1,
        "markup": {"default": {"addPercent": 0, "addFixed": 0}},
        "promotions": [
            {
                "id": "p1",
                "minCartSubtotal": 100,
                "discountType": "percent",
                "discountValue": 5,
                "country": None,
            }
        ],
    }
    assert validate_rules(table_us_ca, body) is None


def test_validate_promotion_percent_over_100(table_us_ca):
    body = {
        "version": 1,
        "markup": {"default": {"addPercent": 0, "addFixed": 0}},
        "promotions": [
            {
                "id": "p1",
                "minCartSubtotal": 0,
                "discountType": "percent",
                "discountValue": 101,
                "country": None,
            }
        ],
    }
    assert validate_rules(table_us_ca, body) == "promotion_0_discount_percent_too_large"


def test_normalize_sorts_promotions_and_countries(table_us_ca):
    body = {
        "version": 2,
        "markup": {
            "default": {"addPercent": 0, "addFixed": 0},
            "byCountry": {"CA": {"addPercent": 1, "addFixed": 0}, "US": {"addPercent": 2, "addFixed": 0}},
        },
        "promotions": [
            {"id": "b", "minCartSubtotal": 50, "discountType": "fixed", "discountValue": 1, "country": "US"},
            {"id": "a", "minCartSubtotal": 10, "discountType": "fixed", "discountValue": 0.5, "country": None},
        ],
    }
    assert validate_rules(table_us_ca, body) is None
    norm = normalize_rules_dict(body)
    assert list(norm["markup"]["byCountry"].keys()) == ["CA", "US"]
    assert [p["id"] for p in norm["promotions"]] == ["a", "b"]


def test_normalize_for_storage_roundtrip(table_us_ca):
    body = {
        "version": 1,
        "markup": {"default": {"addPercent": 1.5, "addFixed": 0}, "byCountry": {}},
        "promotions": [],
    }
    s = normalize_for_storage(body)
    data = json.loads(s)
    assert validate_rules(table_us_ca, data) is None


def test_parse_rules_from_meta_missing(table_us_ca):
    rules, warn = parse_rules_from_meta(table_us_ca, {})
    assert rules == default_rules()
    assert warn is None


def test_parse_rules_from_meta_invalid_json(table_us_ca):
    rules, warn = parse_rules_from_meta(table_us_ca, {"merchant_premium_rules_json": "not-json"})
    assert rules == default_rules()
    assert warn == "invalid_json"


def test_parse_rules_from_meta_schema_invalid(table_us_ca):
    bad = json.dumps(
        {
            "version": 1,
            "markup": {"default": {"addPercent": 0, "addFixed": 0}, "byCountry": {"XX": {"addPercent": 1, "addFixed": 0}}},
            "promotions": [],
        }
    )
    rules, warn = parse_rules_from_meta(table_us_ca, {"merchant_premium_rules_json": bad})
    assert rules == default_rules()
    assert warn and warn.startswith("schema_invalid:")

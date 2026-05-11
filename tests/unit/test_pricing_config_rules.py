"""Pricing model validation: uniqueness, no removals, plan_code vs sku alias."""

import json

import pytest

from lib.pricing_config import (
    normalize_tiers_for_storage,
    put_pricing_model,
    stored_tier_codes,
    validate_tiers,
)


class _FakeTable:
    def __init__(self, item: dict | None):
        self._item = item

    def get_item(self, Key):  # noqa: N802
        return {"Item": self._item} if self._item else {}

    def put_item(self, Item):  # noqa: N802
        self._item = Item
        self.last_put = Item


def test_validate_duplicate_rejected():
    err = validate_tiers(
        [
            {"plan_code": "S1", "price_usd": 1},
            {"plan_code": "S1", "price_usd": 2},
        ]
    )
    assert err and "duplicate" in err


def test_validate_plan_code_sku_mismatch():
    err = validate_tiers([{"plan_code": "A", "sku": "B", "price_usd": 1}])
    assert err and "match" in err


def test_normalize_prefers_single_identifier():
    n = normalize_tiers_for_storage([{"sku": "X1", "price_usd": 2.5}])
    assert n == [{"plan_code": "X1", "price_usd": 2.5}]


def test_put_rejects_removal():
    existing = {
        "pk": "GLOBAL#CONFIG",
        "sk": "PRICING_MODEL_DEFAULT",
        "tiers_json": json.dumps(
            [
                {"plan_code": "KEEP", "price_usd": 1.0},
                {"plan_code": "ALSO", "price_usd": 2.0},
            ]
        ),
    }
    table = _FakeTable(existing)
    with pytest.raises(ValueError, match="cannot_remove_existing_tier_codes"):
        put_pricing_model(
            table,
            [{"plan_code": "KEEP", "price_usd": 9.0}],
            "admin",
        )


def test_put_allows_price_change_same_codes():
    existing = {
        "pk": "GLOBAL#CONFIG",
        "sk": "PRICING_MODEL_DEFAULT",
        "tiers_json": json.dumps([{"plan_code": "A", "price_usd": 1.0}]),
    }
    table = _FakeTable(existing)
    put_pricing_model(table, [{"plan_code": "A", "price_usd": 99.0}], "admin")
    saved = json.loads(table.last_put["tiers_json"])
    assert saved[0]["price_usd"] == 99.0


def test_stored_tier_codes_reads_legacy_sku():
    item = {
        "tiers_json": json.dumps([{"sku": "OLD", "price_usd": 1.0, "min_usd": 0, "max_usd": 1}]),
    }
    table = _FakeTable(item)
    assert stored_tier_codes(table) == {"OLD"}

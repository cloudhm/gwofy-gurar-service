"""Pricing model validation: uniqueness, no removals, plan_code vs sku alias."""

import json

import pytest

from lib.models import PK_GLOBAL_CONFIG, sk_pricing_model
from lib.pricing_config import (
    normalize_tiers_for_storage,
    put_pricing_model,
    stored_tier_codes,
    validate_tiers,
)


class _FakeTable:
    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}

    def get_item(self, Key):  # noqa: N802
        k = (Key["pk"], Key["sk"])
        it = self.items.get(k)
        return {"Item": it} if it else {}

    def put_item(self, Item):  # noqa: N802
        self.items[(Item["pk"], Item["sk"])] = Item
        self.last_put = Item


def test_validate_duplicate_rejected():
    err = validate_tiers(
        [
            {"plan_code": "S1", "price": 1},
            {"plan_code": "S1", "price": 2},
        ]
    )
    assert err and "duplicate" in err


def test_validate_plan_code_sku_mismatch():
    err = validate_tiers([{"plan_code": "A", "sku": "B", "price": 1}])
    assert err and "match" in err


def test_validate_accepts_legacy_price_usd():
    err = validate_tiers([{"plan_code": "X", "price_usd": 2.5}])
    assert err is None


def test_normalize_prefers_single_identifier():
    n = normalize_tiers_for_storage([{"sku": "X1", "price": 2.5}])
    assert n == [{"plan_code": "X1", "price": 2.5}]


def test_put_rejects_removal():
    usd_sk = sk_pricing_model("USD")
    existing = {
        "pk": PK_GLOBAL_CONFIG,
        "sk": usd_sk,
        "tiers_json": json.dumps(
            [
                {"plan_code": "KEEP", "price": 1.0},
                {"plan_code": "ALSO", "price": 2.0},
            ]
        ),
    }
    table = _FakeTable()
    table.items[(existing["pk"], existing["sk"])] = existing
    with pytest.raises(ValueError, match="cannot_remove_existing_tier_codes"):
        put_pricing_model(
            table,
            [{"plan_code": "KEEP", "price": 9.0}],
            "admin",
            currency="USD",
        )


def test_put_allows_price_change_same_codes():
    usd_sk = sk_pricing_model("USD")
    existing = {
        "pk": PK_GLOBAL_CONFIG,
        "sk": usd_sk,
        "tiers_json": json.dumps([{"plan_code": "A", "price": 1.0}]),
    }
    table = _FakeTable()
    table.items[(existing["pk"], existing["sk"])] = existing
    put_pricing_model(table, [{"plan_code": "A", "price": 99.0}], "admin", currency="USD")
    saved = json.loads(table.last_put["tiers_json"])
    assert saved[0]["price"] == 99.0


def test_stored_tier_codes_reads_legacy_sku():
    usd_sk = sk_pricing_model("USD")
    item = {
        "pk": PK_GLOBAL_CONFIG,
        "sk": usd_sk,
        "tiers_json": json.dumps([{"sku": "OLD", "price": 1.0, "min_usd": 0, "max_usd": 1}]),
    }
    table = _FakeTable()
    table.items[(item["pk"], item["sk"])] = item
    assert stored_tier_codes(table, "USD") == {"OLD"}

"""GET /admin/config/pricing-model."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env_table(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")


def test_get_pricing_model_returns_tiers():
    from admin_handler import handler

    tiers = [{"plan_code": "S0001", "price_usd": 0.99}]
    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.get_pricing_model", return_value=tiers),
        patch("admin_handler.ddb.Table", return_value=MagicMock()),
    ):
        out = handler(
            {
                "requestContext": {
                    "http": {"method": "GET", "path": "/admin/config/pricing-model"},
                },
                "headers": {},
            },
            None,
        )
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body == {"tiers": tiers}

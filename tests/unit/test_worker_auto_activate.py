"""Worker auto-activate after install profile sync."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "test-client")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "test-secret")

from unittest.mock import MagicMock, patch

from lib.activate_app import ActivateAppError
from worker_handler import _maybe_auto_activate


def test_auto_activate_skips_when_already_activated():
    table = MagicMock()
    with patch("worker_handler.run_activate_app_safe") as run:
        _maybe_auto_activate(
            table,
            "a.myshopify.com",
            "1001",
            "token",
            "kms-key",
            "2026-04",
            {"activation_status": "ACTIVATED"},
        )
    run.assert_not_called()


def test_auto_activate_calls_run_activate_when_unactivated():
    table = MagicMock()
    with patch("worker_handler.run_activate_app_safe") as run:
        _maybe_auto_activate(
            table,
            "a.myshopify.com",
            "1001",
            "token",
            "kms-key",
            "2026-04",
            {"activation_status": "UNACTIVATED"},
        )
    run.assert_called_once_with(
        table,
        "a.myshopify.com",
        "1001",
        "token",
        "kms-key",
        "2026-04",
        actor_sub="initial_sync",
    )


def test_auto_activate_swallows_business_error():
    table = MagicMock()
    with patch(
        "worker_handler.run_activate_app_safe",
        side_effect=ActivateAppError("pricing_not_configured", "no tiers"),
    ):
        _maybe_auto_activate(
            table,
            "a.myshopify.com",
            "1001",
            "token",
            "kms-key",
            "2026-04",
            {},
        )


def test_auto_activate_swallows_upstream_error():
    table = MagicMock()
    with patch(
        "worker_handler.run_activate_app_safe",
        side_effect=RuntimeError("shopify down"),
    ):
        _maybe_auto_activate(
            table,
            "a.myshopify.com",
            "1001",
            "token",
            "kms-key",
            "2026-04",
            {},
        )

"""Idempotency for Shopify webhook deliveries (X-Shopify-Webhook-Id)."""

from __future__ import annotations

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from unittest.mock import MagicMock, patch

from worker_handler import process_webhook_envelope
from lib.models import SK_WEBHOOK_PROCESSED, pk_webhook


def _envelope(webhook_id: str, topic: str, shop: str = "s.myshopify.com") -> dict:
    return {
        "source": "webhook_ingress",
        "headers": {
            "x-shopify-webhook-id": webhook_id,
            "x-shopify-topic": topic,
            "x-shopify-shop-domain": shop,
        },
        "body": "{}",
    }


@patch("worker_handler.handle_uninstall")
def test_duplicate_webhook_id_skips_handler(mock_uninstall):
    wid = "11111111-1111-1111-1111-111111111111"
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {"pk": pk_webhook(wid), "sk": SK_WEBHOOK_PROCESSED}
    }

    process_webhook_envelope(
        table, _envelope(wid, "app/uninstalled"), "kms-alias", "2024-10", ""
    )

    mock_uninstall.assert_not_called()
    table.put_item.assert_not_called()


@patch("worker_handler.handle_uninstall")
def test_first_delivery_runs_then_marks(mock_uninstall):
    wid = "22222222-2222-2222-2222-222222222222"
    table = MagicMock()
    table.get_item.return_value = {}

    process_webhook_envelope(
        table, _envelope(wid, "app/uninstalled"), "kms-alias", "2024-10", ""
    )

    mock_uninstall.assert_called_once()
    table.put_item.assert_called_once()
    call_kw = table.put_item.call_args.kwargs["Item"]
    assert call_kw["pk"] == pk_webhook(wid)
    assert call_kw["sk"] == SK_WEBHOOK_PROCESSED
    assert call_kw["topic"] == "app/uninstalled"


@patch("worker_handler.handle_uninstall")
def test_empty_webhook_id_no_dedupe_row(mock_uninstall):
    table = MagicMock()
    table.get_item.return_value = {}

    env = _envelope("", "app/uninstalled")
    env["headers"]["x-shopify-webhook-id"] = ""

    process_webhook_envelope(table, env, "kms-alias", "2024-10", "")

    mock_uninstall.assert_called_once()
    dedupe_puts = [
        c
        for c in table.put_item.call_args_list
        if c.kwargs.get("Item", {}).get("sk") == SK_WEBHOOK_PROCESSED
    ]
    assert dedupe_puts == []

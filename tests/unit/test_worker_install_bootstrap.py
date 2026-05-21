"""Install bootstrap enqueues async catalog sync."""

import json
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "test-client")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "test-secret")
os.environ.setdefault("WORK_QUEUE_URL", "https://sqs.example.com/queue")
os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("KMS_KEY_ID", "kms-key")

from unittest.mock import MagicMock, patch

from worker_handler import _enqueue_catalog_sync, run_install_bootstrap


def test_enqueue_catalog_sync_sends_oauth_event():
    with patch("worker_handler.sqs") as sqs_mock:
        _enqueue_catalog_sync("a.myshopify.com", "1001", "2026-04")
    sqs_mock.send_message.assert_called_once()
    body = json.loads(sqs_mock.send_message.call_args.kwargs["MessageBody"])
    assert body == {
        "source": "oauth",
        "event": "CATALOG_SYNC",
        "shop": "a.myshopify.com",
        "store_number": "1001",
        "api_version": "2026-04",
    }


def test_install_bootstrap_enqueues_catalog_by_default():
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {
            "installation_status": "ACTIVE",
            "access_token_enc": "enc",
            "activation_status": "UNACTIVATED",
        }
    }
    with (
        patch("worker_handler._shop_admin_token", return_value="token"),
        patch("worker_handler._ensure_global_config_seeds"),
        patch("worker_handler.sync_shop_profile"),
        patch("worker_handler._maybe_auto_activate"),
        patch("worker_handler._enqueue_catalog_sync") as enqueue_catalog,
        patch("worker_handler._enqueue_theme_sync") as enqueue_theme,
    ):
        run_install_bootstrap(table, "a.myshopify.com", "1001", "kms-key", "2026-04")
    enqueue_catalog.assert_called_once_with("a.myshopify.com", "1001", "2026-04")
    enqueue_theme.assert_called_once_with("a.myshopify.com", "1001", "2026-04")


def test_install_bootstrap_skips_enqueue_when_disabled():
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {
            "installation_status": "ACTIVE",
            "access_token_enc": "enc",
        }
    }
    with (
        patch("worker_handler._shop_admin_token", return_value="token"),
        patch("worker_handler._ensure_global_config_seeds"),
        patch("worker_handler.sync_shop_profile"),
        patch("worker_handler._maybe_auto_activate"),
        patch("worker_handler._enqueue_catalog_sync") as enqueue_catalog,
        patch("worker_handler._enqueue_theme_sync") as enqueue_theme,
    ):
        run_install_bootstrap(
            table,
            "a.myshopify.com",
            "1001",
            "kms-key",
            "2026-04",
            enqueue_catalog=False,
            enqueue_themes=False,
        )
    enqueue_catalog.assert_not_called()
    enqueue_theme.assert_not_called()

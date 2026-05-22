"""POST /api/install — session token → offline tokens + shop bootstrap."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.modules.setdefault("jwt", MagicMock())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("WORK_QUEUE_URL", "https://sqs.example/queue")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "sec")
    monkeypatch.setenv("KMS_KEY_ID", "kms")
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-04")


def _session_event():
    return {
        "requestContext": {"http": {"method": "POST", "path": "/api/install"}, "requestId": "r1"},
        "headers": {"authorization": "Bearer sess.jwt"},
    }


def test_install_bootstraps_new_shop():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.side_effect = [
        {"Item": None},
        {
            "Item": {
                "store_number": "1000000001",
                "installation_status": "ACTIVE",
                "activation_status": "UNACTIVATED",
            }
        },
    ]
    token_resp = {
        "access_token": "offline-at",
        "refresh_token": "offline-rt",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
        "scope": "read_products",
    }

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
        patch(
            "merchant_api_handler.exchange_session_token_for_offline_access",
            return_value=token_resp,
        ),
        patch(
            "merchant_api_handler.upsert_shop_metadata_from_offline_tokens",
            return_value="1000000001",
        ) as upsert,
        patch("merchant_api_handler.enqueue_install_worker_jobs") as enq,
        patch("merchant_api_handler.append_audit"),
        patch("merchant_api_handler._try_recover_offline_token_from_session", return_value=None),
    ):
        out = handler(_session_event(), None)

    upsert.assert_called_once()
    enq.assert_called_once()
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["ok"] is True
    assert body["bootstrap_enqueued"] is True
    assert body["store_number"] == "1000000001"


def test_install_refreshes_existing_active_shop_without_enqueue():
    from merchant_api_handler import handler

    meta = {
        "pk": "SHOP#a.myshopify.com",
        "sk": "METADATA",
        "installation_status": "ACTIVE",
        "access_token_enc": "enc",
        "store_number": "1000000099",
        "activation_status": "ACTIVATED",
    }
    tbl = MagicMock()
    tbl.get_item.side_effect = [{"Item": meta}, {"Item": meta}]
    token_resp = {
        "access_token": "new-at",
        "refresh_token": "new-rt",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
    }

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
        patch(
            "merchant_api_handler.exchange_session_token_for_offline_access",
            return_value=token_resp,
        ),
        patch("merchant_api_handler.persist_expiring_offline_tokens") as pers,
        patch("merchant_api_handler.upsert_shop_metadata_from_offline_tokens") as upsert,
        patch("merchant_api_handler.enqueue_install_worker_jobs") as enq,
        patch("merchant_api_handler.append_audit"),
        patch("merchant_api_handler._try_recover_offline_token_from_session", return_value=None),
    ):
        out = handler(_session_event(), None)

    pers.assert_called_once()
    upsert.assert_not_called()
    enq.assert_not_called()
    body = json.loads(out["body"])
    assert body["bootstrap_enqueued"] is False
    assert body["store_number"] == "1000000099"


def test_install_token_exchange_failure_401():
    from merchant_api_handler import handler

    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": None}
    err = requests.HTTPError("bad")
    err.response = MagicMock(status_code=400, text="invalid_grant")

    with (
        patch("merchant_api_handler.verify_session_token", return_value={"sub": "u1"}),
        patch("merchant_api_handler.shop_host_from_payload", return_value="a.myshopify.com"),
        patch("merchant_api_handler.ddb.Table", return_value=tbl),
        patch(
            "merchant_api_handler.exchange_session_token_for_offline_access",
            side_effect=err,
        ),
        patch("merchant_api_handler.append_audit"),
        patch("merchant_api_handler._try_recover_offline_token_from_session", return_value=None),
    ):
        out = handler(_session_event(), None)

    assert out["statusCode"] == 401
    assert json.loads(out["body"])["error"] == "session_token_exchange_failed"

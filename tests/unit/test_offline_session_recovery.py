"""Session-token exchange recovery for expiring offline tokens."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.modules.setdefault("jwt", MagicMock())

from lib.models import SK_METADATA, pk_shop
from lib.shop_offline_access import (
    INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED,
    REFRESH_ENC,
    REFRESH_EXPIRES_AT,
    ShopifyAuth401Error,
    offline_token_needs_session_recovery,
    offline_token_recovery_reason,
    recover_offline_token_from_session,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_recovery_reason_offline_auth_expired():
    meta = {"access_token_enc": "enc", "installation_status": INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED}
    assert offline_token_recovery_reason(meta) == "offline_auth_expired"
    assert offline_token_needs_session_recovery(meta) is True


def test_recovery_reason_missing_refresh():
    meta = {"access_token_enc": "enc", "installation_status": "ACTIVE"}
    assert offline_token_recovery_reason(meta) == "missing_refresh"


def test_recovery_reason_refresh_near_expiry():
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc",
        REFRESH_ENC: "enc-r",
        REFRESH_EXPIRES_AT: _iso(now + timedelta(days=3)),
        "installation_status": "ACTIVE",
    }
    with patch.dict("os.environ", {"OFFLINE_REFRESH_RECOVERY_WINDOW_DAYS": "7"}):
        assert offline_token_recovery_reason(meta, now) == "refresh_near_expiry"


def test_recovery_reason_none_when_refresh_healthy():
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc",
        REFRESH_ENC: "enc-r",
        REFRESH_EXPIRES_AT: _iso(now + timedelta(days=60)),
        "installation_status": "ACTIVE",
    }
    with patch.dict("os.environ", {"OFFLINE_REFRESH_RECOVERY_WINDOW_DAYS": "7"}):
        assert offline_token_recovery_reason(meta, now) is None


def test_recover_exchanges_and_persists():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc-a",
        "kms_key_id": "arn:kms:1",
        "installation_status": INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED,
        REFRESH_ENC: "enc-r",
        REFRESH_EXPIRES_AT: _iso(now - timedelta(hours=1)),
    }
    table.get_item.return_value = {
        "Item": {
            **meta,
            "installation_status": "ACTIVE",
            "access_token_enc": "enc-new",
        }
    }
    exchange_resp = {
        "access_token": "new-at",
        "refresh_token": "new-rt",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
        "scope": "read_products",
    }
    with (
        patch(
            "lib.shop_offline_access.exchange_session_token_for_offline_access",
            return_value=exchange_resp,
        ) as ex,
        patch("lib.shop_offline_access.persist_expiring_offline_tokens") as pers,
    ):
        out = recover_offline_token_from_session(
            table,
            "s.myshopify.com",
            "session.jwt",
            kms_key_id="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    ex.assert_called_once_with("s.myshopify.com", "cid", "sec", "session.jwt")
    pers.assert_called_once()
    assert out.get("installation_status") == "ACTIVE"


def test_recover_raises_on_exchange_http_error():
    table = MagicMock()
    meta = {
        "access_token_enc": "enc",
        "installation_status": INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED,
    }
    err = requests.HTTPError("bad")
    err.response = MagicMock(status_code=400, text="invalid session")
    with (
        patch(
            "lib.shop_offline_access.exchange_session_token_for_offline_access",
            side_effect=err,
        ),
        pytest.raises(ShopifyAuth401Error) as exc,
    ):
        recover_offline_token_from_session(
            table,
            "s.myshopify.com",
            "bad.jwt",
            kms_key_id="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    assert exc.value.requires_reauthorize is True


def test_try_recover_invokes_exchange_when_expired(monkeypatch):
    monkeypatch.setenv("KMS_KEY_ID", "kms")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "sec")

    now = datetime.now(timezone.utc)
    expired_item = {
        "access_token_enc": "enc",
        "installation_status": INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED,
        REFRESH_ENC: "enc-r",
        REFRESH_EXPIRES_AT: _iso(now - timedelta(hours=1)),
    }
    tbl = MagicMock()
    tbl.get_item.return_value = {"Item": expired_item}

    with (
        patch("boto3.resource"),
        patch("boto3.client"),
        patch("merchant_api_handler.recover_offline_token_from_session") as rec,
        patch("merchant_api_handler.append_audit"),
    ):
        from merchant_api_handler import _try_recover_offline_token_from_session

        out = _try_recover_offline_token_from_session(
            tbl,
            "a.myshopify.com",
            "sess.tok",
            {"sub": "u1"},
            {},
            "r1",
        )
    rec.assert_called_once()
    assert out is None

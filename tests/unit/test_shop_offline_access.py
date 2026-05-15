"""shop_offline_access: expiring offline token resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from lib.models import SK_METADATA, pk_shop
from lib.shop_offline_access import (
    ACCESS_EXPIRES_AT,
    INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED,
    REFRESH_ENC,
    REFRESH_EXPIRES_AT,
    get_fresh_shop_access_token,
    persist_expiring_offline_tokens,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_get_fresh_returns_cached_when_access_not_expired():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc-a",
        REFRESH_ENC: "enc-r",
        ACCESS_EXPIRES_AT: _iso(now + timedelta(hours=1)),
    }
    with (
        patch("lib.shop_offline_access.decrypt_token", return_value="plain-access") as dec,
        patch("lib.shop_offline_access.refresh_offline_access_token") as ref,
    ):
        out = get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    assert out == "plain-access"
    dec.assert_called_once()
    ref.assert_not_called()


def test_get_fresh_refreshes_when_access_near_expiry():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc-a",
        REFRESH_ENC: "enc-r",
        "kms_key_id": "arn:kms:1",
        ACCESS_EXPIRES_AT: _iso(now + timedelta(seconds=30)),
        "shopify_offline_refresh_token_expires_at": _iso(now + timedelta(days=30)),
    }
    new_resp = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
    }
    with (
        patch("lib.shop_offline_access.decrypt_token", return_value="old-access"),
        patch("lib.shop_offline_access.decrypt_refresh_token", return_value="old-refresh"),
        patch("lib.shop_offline_access.refresh_offline_access_token", return_value=new_resp) as ref,
        patch("lib.shop_offline_access.persist_expiring_offline_tokens") as pers,
    ):
        out = get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    assert out == "new-access"
    ref.assert_called_once_with("s.myshopify.com", "cid", "sec", "old-refresh")
    pers.assert_called_once()
    assert pers.call_args[0][1] == "s.myshopify.com"
    assert pers.call_args[0][2] == "arn:kms:1"


def test_get_fresh_marks_installation_when_refresh_calendar_expired():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc-a",
        REFRESH_ENC: "enc-r",
        "kms_key_id": "arn:kms:1",
        ACCESS_EXPIRES_AT: _iso(now - timedelta(hours=1)),
        REFRESH_EXPIRES_AT: _iso(now - timedelta(hours=1)),
    }
    with (
        patch("lib.shop_offline_access.decrypt_token", return_value="old-access"),
        pytest.raises(RuntimeError, match="reauthorize"),
    ):
        get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    table.update_item.assert_called_once()
    vals = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert vals[":s"] == INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED


def _http_401(*_args, **_kwargs) -> None:
    resp = MagicMock()
    resp.status_code = 401
    raise requests.HTTPError(response=resp)


def test_get_fresh_marks_installation_when_refresh_returns_401():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc-a",
        REFRESH_ENC: "enc-r",
        "kms_key_id": "arn:kms:1",
        ACCESS_EXPIRES_AT: _iso(now - timedelta(hours=1)),
        REFRESH_EXPIRES_AT: _iso(now + timedelta(days=30)),
    }
    with (
        patch("lib.shop_offline_access.decrypt_token", return_value="old-access"),
        patch("lib.shop_offline_access.decrypt_refresh_token", return_value="rt-plain"),
        patch(
            "lib.shop_offline_access.refresh_offline_access_token",
            side_effect=_http_401,
        ),
        pytest.raises(requests.HTTPError),
    ):
        get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    table.update_item.assert_called_once()
    vals = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert vals[":s"] == INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED


def test_get_fresh_marks_installation_when_migrate_returns_401():
    table = MagicMock()
    meta = {
        "access_token_enc": "enc-a",
        "kms_key_id": "arn:kms:1",
    }
    with (
        patch("lib.shop_offline_access.decrypt_token", return_value="legacy"),
        patch(
            "lib.shop_offline_access.migrate_non_expiring_offline_token",
            side_effect=_http_401,
        ),
        pytest.raises(requests.HTTPError),
    ):
        get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    table.update_item.assert_called_once()
    vals = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert vals[":s"] == INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED


def test_get_fresh_migrates_when_no_refresh():
    table = MagicMock()
    meta = {
        "access_token_enc": "enc-a",
        "kms_key_id": "arn:kms:1",
    }
    new_resp = {
        "access_token": "m-access",
        "refresh_token": "m-refresh",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
    }
    with (
        patch("lib.shop_offline_access.decrypt_token", return_value="legacy"),
        patch("lib.shop_offline_access.migrate_non_expiring_offline_token", return_value=new_resp) as mig,
        patch("lib.shop_offline_access.persist_expiring_offline_tokens") as pers,
    ):
        out = get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    assert out == "m-access"
    mig.assert_called_once_with("s.myshopify.com", "cid", "sec", "legacy")
    pers.assert_called_once()


def test_persist_calls_update_with_refresh():
    table = MagicMock()
    with patch("lib.shop_offline_access.encrypt_token", return_value="A"), patch(
        "lib.shop_offline_access.encrypt_refresh_token", return_value="R"
    ):
        persist_expiring_offline_tokens(
            table,
            "s.myshopify.com",
            "kms",
            {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 60,
                "refresh_token_expires_in": 90,
            },
        )
    table.update_item.assert_called_once()
    kw = table.update_item.call_args.kwargs
    assert kw["Key"] == {"pk": pk_shop("s.myshopify.com"), "sk": SK_METADATA}
    assert ":r" in kw["ExpressionAttributeValues"]

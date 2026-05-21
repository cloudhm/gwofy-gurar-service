"""shop_offline_access: expiring offline token resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from lib.models import SK_METADATA, pk_shop
from lib.shop_offline_access import (
    ACCESS_EXPIRES_AT,
    API_OAUTH_REFRESH_OFFLINE,
    INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED,
    LAST_OFFLINE_AUTH_401_API,
    LOCK_AFTER_CONSECUTIVE_401,
    OFFLINE_AUTH_401_CONSECUTIVE_COUNT,
    REFRESH_ENC,
    REFRESH_EXPIRES_AT,
    ShopifyAuth401Error,
    bump_offline_auth_401_failure,
    get_fresh_shop_access_token,
    persist_expiring_offline_tokens,
    retry_shop_offline_token_refresh,
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
        pytest.raises(ShopifyAuth401Error) as exc,
    ):
        get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    assert exc.value.requires_reauthorize is True
    table.update_item.assert_called_once()
    vals = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert vals[":s"] == INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED


def _http_401(*_args, **_kwargs) -> None:
    resp = MagicMock()
    resp.status_code = 401
    raise requests.HTTPError(response=resp)


def test_get_fresh_records_401_but_stays_active_when_refresh_calendar_valid():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc-a",
        REFRESH_ENC: "enc-r",
        "kms_key_id": "arn:kms:1",
        ACCESS_EXPIRES_AT: _iso(now - timedelta(hours=1)),
        REFRESH_EXPIRES_AT: _iso(now + timedelta(days=30)),
    }
    table.update_item.return_value = {"Attributes": {OFFLINE_AUTH_401_CONSECUTIVE_COUNT: 1}}
    with (
        patch("lib.shop_offline_access.decrypt_token", return_value="old-access"),
        patch("lib.shop_offline_access.decrypt_refresh_token", return_value="rt-plain"),
        patch("lib.shop_offline_access.refresh_offline_access_token", side_effect=_http_401),
        patch("lib.shop_offline_access.time.sleep"),
        pytest.raises(ShopifyAuth401Error) as exc,
    ):
        get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
        )
    assert exc.value.api_name == API_OAUTH_REFRESH_OFFLINE
    assert exc.value.requires_reauthorize is False
    lock_calls = [
        c
        for c in table.update_item.call_args_list
        if (c.kwargs.get("ExpressionAttributeValues") or {}).get(":s")
        == INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED
    ]
    assert len(lock_calls) == 0


def test_bump_locks_after_threshold_when_refresh_calendar_valid():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    refresh_exp = now + timedelta(days=30)
    table.update_item.return_value = {
        "Attributes": {OFFLINE_AUTH_401_CONSECUTIVE_COUNT: LOCK_AFTER_CONSECUTIVE_401}
    }
    count, locked = bump_offline_auth_401_failure(
        table,
        "s.myshopify.com",
        API_OAUTH_REFRESH_OFFLINE,
        refresh_exp,
        now,
    )
    assert count == LOCK_AFTER_CONSECUTIVE_401
    assert locked is True
    lock_vals = [
        c.kwargs["ExpressionAttributeValues"]
        for c in table.update_item.call_args_list
        if (c.kwargs.get("ExpressionAttributeValues") or {}).get(":s")
        == INSTALLATION_STATUS_OFFLINE_AUTH_EXPIRED
    ]
    assert len(lock_vals) == 1


def test_get_fresh_force_refresh_skips_cached_access():
    table = MagicMock()
    now = datetime.now(timezone.utc)
    meta = {
        "access_token_enc": "enc-a",
        REFRESH_ENC: "enc-r",
        "kms_key_id": "arn:kms:1",
        ACCESS_EXPIRES_AT: _iso(now + timedelta(hours=2)),
        REFRESH_EXPIRES_AT: _iso(now + timedelta(days=30)),
    }
    new_resp = {"access_token": "forced", "refresh_token": "r", "expires_in": 60, "refresh_token_expires_in": 90}
    with (
        patch("lib.shop_offline_access.decrypt_token", return_value="cached"),
        patch("lib.shop_offline_access.decrypt_refresh_token", return_value="rt"),
        patch("lib.shop_offline_access.refresh_offline_access_token", return_value=new_resp) as ref,
        patch("lib.shop_offline_access.persist_expiring_offline_tokens"),
    ):
        out = get_fresh_shop_access_token(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
            meta=meta,
            force_refresh=True,
        )
    assert out == "forced"
    ref.assert_called_once()


def test_retry_shop_offline_token_refresh():
    table = MagicMock()
    row_after = {
        "installation_status": "ACTIVE",
        ACCESS_EXPIRES_AT: "2026-06-01T00:00:00+00:00",
        REFRESH_EXPIRES_AT: "2026-09-01T00:00:00+00:00",
    }
    table.get_item.return_value = {"Item": row_after}
    with patch(
        "lib.shop_offline_access.get_fresh_shop_access_token", return_value="tok12345678"
    ) as gf:
        out = retry_shop_offline_token_refresh(
            table,
            "s.myshopify.com",
            kms_key_id_fallback="kms",
            client_id="cid",
            client_secret="sec",
        )
    gf.assert_called_once()
    assert gf.call_args.kwargs["force_refresh"] is True
    assert out["ok"] is True
    assert out["installation_status"] == "ACTIVE"


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
    assert ":active" in kw["ExpressionAttributeValues"]
    assert OFFLINE_AUTH_401_CONSECUTIVE_COUNT in kw["UpdateExpression"]

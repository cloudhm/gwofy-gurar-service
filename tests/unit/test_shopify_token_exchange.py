"""Shopify OAuth token exchange (session → offline)."""

from unittest.mock import MagicMock, patch

from lib.shopify_api import exchange_session_token_for_offline_access


def test_exchange_session_token_for_offline_access_posts_expected_fields():
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 3600,
        "refresh_token_expires_in": 7776000,
    }
    with patch("lib.shopify_api.requests.post", return_value=resp) as post:
        out = exchange_session_token_for_offline_access(
            "shop.myshopify.com",
            "client-id",
            "client-secret",
            "session.jwt.here",
        )
    assert out["access_token"] == "at"
    post.assert_called_once()
    _args, kwargs = post.call_args
    assert _args[0] == "https://shop.myshopify.com/admin/oauth/access_token"
    data = kwargs["data"]
    assert data["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert data["subject_token"] == "session.jwt.here"
    assert data["subject_token_type"] == "urn:ietf:params:oauth:token-type:id_token"
    assert data["requested_token_type"] == "urn:shopify:params:oauth:token-type:offline-access-token"
    assert data["expiring"] == "1"

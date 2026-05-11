"""Tests for Cognito /auth/callback token exchange."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

from cognito_callback_handler import handler


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_callback_returns_html_with_tokens(monkeypatch):
    monkeypatch.setenv("COGNITO_CLIENT_ID", "cid")
    monkeypatch.setenv("COGNITO_REDIRECT_URI", "https://api.example.com/auth/callback")
    monkeypatch.setenv("COGNITO_HOSTED_UI_DOMAIN", "p.auth.ap-east-1.amazoncognito.com")

    payload = json.dumps(
        {"id_token": "eyJid", "access_token": "at", "token_type": "Bearer", "expires_in": 3600}
    ).encode()

    with patch("cognito_callback_handler.urllib.request.urlopen", return_value=_FakeResp(payload)):
        out = handler(
            {"queryStringParameters": {"code": "abc123"}, "headers": {}},
            None,
        )

    assert out["statusCode"] == 200
    assert "text/html" in out["headers"]["Content-Type"]
    assert "eyJid" in out["body"]
    assert "/admin" in out["body"] or "Id token" in out["body"]
    assert "No refresh token" in out["body"]


def test_callback_html_includes_refresh_token_when_present(monkeypatch):
    monkeypatch.setenv("COGNITO_CLIENT_ID", "cid")
    monkeypatch.setenv("COGNITO_REDIRECT_URI", "https://api.example.com/auth/callback")
    monkeypatch.setenv("COGNITO_HOSTED_UI_DOMAIN", "p.auth.ap-east-1.amazoncognito.com")

    payload = json.dumps(
        {
            "id_token": "eyJid",
            "access_token": "at",
            "refresh_token": "rt-secret-value",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
    ).encode()

    with patch("cognito_callback_handler.urllib.request.urlopen", return_value=_FakeResp(payload)):
        out = handler(
            {"queryStringParameters": {"code": "abc123"}, "headers": {}},
            None,
        )

    assert out["statusCode"] == 200
    body = out["body"]
    assert "Refresh token" in body
    assert "rt-secret-value" in body
    assert "grant_type=refresh_token" in body
    assert "No refresh token" not in body


def test_callback_json_accept_header(monkeypatch):
    monkeypatch.setenv("COGNITO_CLIENT_ID", "cid")
    monkeypatch.setenv("COGNITO_REDIRECT_URI", "https://api.example.com/auth/callback")
    monkeypatch.setenv("COGNITO_HOSTED_UI_DOMAIN", "p.auth.ap-east-1.amazoncognito.com")

    payload = json.dumps({"id_token": "id.tok", "access_token": "at"}).encode()

    with patch("cognito_callback_handler.urllib.request.urlopen", return_value=_FakeResp(payload)):
        out = handler(
            {
                "queryStringParameters": {"code": "x"},
                "headers": {"accept": "application/json"},
            },
            None,
        )

    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["id_token"] == "id.tok"


def test_callback_cognito_error_query(monkeypatch):
    monkeypatch.setenv("COGNITO_CLIENT_ID", "cid")
    monkeypatch.setenv("COGNITO_REDIRECT_URI", "https://api.example.com/auth/callback")
    monkeypatch.setenv("COGNITO_HOSTED_UI_DOMAIN", "p.auth.ap-east-1.amazoncognito.com")

    out = handler(
        {"queryStringParameters": {"error": "access_denied", "error_description": "cancel"}},
        None,
    )
    assert out["statusCode"] == 400
    assert "access_denied" in out["body"]

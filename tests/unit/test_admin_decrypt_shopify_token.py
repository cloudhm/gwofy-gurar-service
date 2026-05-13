"""POST /admin/tools/decrypt-shopify-token."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("KMS_KEY_ID", "arn:aws:kms:ap-east-1:123456789:key/test-key")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")


def _event(body: dict) -> dict:
    return {
        "requestContext": {
            "http": {"method": "POST", "path": "/admin/tools/decrypt-shopify-token"},
            "authorizer": {"jwt": {"claims": {"sub": "admin-1", "email": "a@ex.com"}}},
        },
        "body": json.dumps(body),
        "headers": {},
    }


def test_decrypt_requires_access_token_enc():
    from admin_handler import handler

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=MagicMock()),
    ):
        out = handler(_event({}), None)
    assert out["statusCode"] == 400
    assert json.loads(out["body"])["error"] == "access_token_enc_required"


def test_decrypt_success():
    from admin_handler import handler

    table = MagicMock()
    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=table),
        patch("admin_handler.decrypt_token", return_value="shpat_xx_plain"),
        patch("admin_handler.append_audit") as mock_audit,
    ):
        out = handler(_event({"access_token_enc": "AQIDAHxxx"}), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body == {"ok": True, "access_token": "shpat_xx_plain"}
    mock_audit.assert_called_once()
    kw = mock_audit.call_args.kwargs
    assert kw["action"] == "ADMIN_DECRYPT_SHOPIFY_TOKEN"
    assert kw["outcome"] == "ok"
    assert "plaintext_len" in kw["detail"]


def test_decrypt_failure_returns_502():
    from admin_handler import handler

    table = MagicMock()
    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=table),
        patch("admin_handler.decrypt_token", side_effect=RuntimeError("InvalidCiphertextException")),
        patch("admin_handler.append_audit") as mock_audit,
    ):
        out = handler(_event({"access_token_enc": "bad"}), None)
    assert out["statusCode"] == 502
    assert json.loads(out["body"])["error"] == "decrypt_failed"
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["outcome"] == "error"

import pytest

from gwofy_guard_service.deploy_config import (
    normalize_https_base,
    resolve_admin_cognito_jwt_audiences,
    resolve_deploy_urls,
    suggested_custom_fqdn,
)


def test_suggested_custom_fqdn():
    assert suggested_custom_fqdn(stage="dev") == "sp-dev.gwofy.com"
    assert suggested_custom_fqdn(stage="stage", subdomain_prefix="sp") == "sp-stage.gwofy.com"


def test_normalize_https_base():
    assert normalize_https_base("http://x.com/") == "https://x.com"
    assert normalize_https_base("https://x.com/foo/") == "https://x.com/foo"


def test_resolve_uses_explicit_when_no_cert():
    r = resolve_deploy_urls(
        stage="dev",
        certificate_arn=None,
        domain_base="gwofy.com",
        subdomain_prefix="sp",
        custom_domain_name=None,
        webhook_base_url_env="https://abc.execute-api.us-east-1.amazonaws.com",
        webhook_base_url_ctx=None,
        strict_deploy_config=False,
    )
    assert r.custom_domain_fqdn is None
    assert r.certificate_arn is None
    assert r.webhook_base_url == "https://abc.execute-api.us-east-1.amazonaws.com"


def test_resolve_defaults_webhook_to_custom_domain_when_cert():
    r = resolve_deploy_urls(
        stage="dev",
        certificate_arn="arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        domain_base="gwofy.com",
        subdomain_prefix="sp",
        custom_domain_name=None,
        webhook_base_url_env=None,
        webhook_base_url_ctx=None,
        strict_deploy_config=False,
    )
    assert r.custom_domain_fqdn == "sp-dev.gwofy.com"
    assert r.webhook_base_url == "https://sp-dev.gwofy.com"


def test_resolve_strict_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        resolve_deploy_urls(
            stage="dev",
            certificate_arn="arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            domain_base="gwofy.com",
            subdomain_prefix="sp",
            custom_domain_name=None,
            webhook_base_url_env="https://wrong.example.com",
            webhook_base_url_ctx=None,
            strict_deploy_config=True,
        )


def test_resolve_admin_cognito_jwt_audiences_primary_only():
    assert resolve_admin_cognito_jwt_audiences(primary_client_id="abc") == ["abc"]


def test_resolve_admin_cognito_jwt_audiences_with_extras():
    assert resolve_admin_cognito_jwt_audiences(
        primary_client_id="abc",
        extra_client_ids="xyz, def  ghi",
    ) == ["abc", "xyz", "def", "ghi"]


def test_resolve_admin_cognito_jwt_audiences_dedupes():
    assert resolve_admin_cognito_jwt_audiences(
        primary_client_id="abc",
        extra_client_ids="abc, xyz",
    ) == ["abc", "xyz"]


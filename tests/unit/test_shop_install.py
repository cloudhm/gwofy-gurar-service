"""shop_install helpers."""

from lib.shop_install import shop_needs_install_bootstrap


def test_shop_needs_install_bootstrap_when_missing_row():
    assert shop_needs_install_bootstrap(None) is True


def test_shop_needs_install_bootstrap_when_no_token():
    assert shop_needs_install_bootstrap({"installation_status": "ACTIVE"}) is True


def test_shop_needs_install_bootstrap_when_active_with_token():
    assert (
        shop_needs_install_bootstrap(
            {"installation_status": "ACTIVE", "access_token_enc": "enc"}
        )
        is False
    )

"""merchant_app_config validation and merge."""

from lib.merchant_app_config import (
    merge_app_config,
    parse_app_config_from_meta,
    validate_patch,
)


def test_validate_patch_coupon_code():
    patch, err = validate_patch({"couponCode": "SAVE-10"})
    assert err is None
    assert patch == {"couponCode": "SAVE-10"}


def test_validate_patch_remove_coupon():
    patch, err = validate_patch({"couponCode": None})
    assert err is None
    assert patch == {"couponCode": None}


def test_validate_patch_rejects_empty_coupon():
    _, err = validate_patch({"couponCode": "  "})
    assert err == "couponCode_empty"


def test_validate_patch_rejects_unknown_key():
    _, err = validate_patch({"other": "x"})
    assert err == "invalid_keys"


def test_parse_app_config_from_meta_invalid_json():
    assert parse_app_config_from_meta({"merchant_app_config_json": "not-json"}) == {}


def test_merge_app_config():
    merged = merge_app_config({"couponCode": "A"}, {"couponCode": None})
    assert merged == {}

    merged = merge_app_config({}, {"couponCode": "B"})
    assert merged == {"couponCode": "B"}

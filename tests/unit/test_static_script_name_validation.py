"""Static script filename validation."""

import pytest

from lib.static_scripts import (
    script_name_rules,
    validate_app_config_source,
    validate_script_name,
)


def test_script_name_rules_documented():
    rules = script_name_rules()
    assert "app-config.js" in rules["examplesValid"]
    assert "store1.js" in rules["examplesValid"]
    assert "店铺.js" in rules["examplesInvalid"]
    assert "reservedNames" not in rules


@pytest.mark.parametrize(
    "name",
    [
        "store1.js",
        "patch-v2.js",
        "app-config.js",
        "app.storefront.js",
        "A1_b-2.js",
    ],
)
def test_valid_names(name):
    assert validate_script_name(name) == name


@pytest.mark.parametrize(
    "name,expected_code",
    [
        ("store 1.js", "script_name_whitespace"),
        ("store\t1.js", "script_name_whitespace"),
        ("店铺.js", "script_name_non_ascii"),
        ("store/1.js", "script_name_path_chars"),
        ("..js", "script_name_path_chars"),
        ("-bad.js", "script_name_invalid_format"),
        ("noext", "script_name_invalid_format"),
        ("", "script_name_required"),
    ],
)
def test_invalid_names(name, expected_code):
    with pytest.raises(ValueError, match=expected_code):
        validate_script_name(name)


def test_validate_app_config_source_accepts_literal_assignment():
    validate_app_config_source("g.GWOFY_CONFIG = { debug: true };")
    validate_app_config_source("g.GWOFY_CONFIG = Object.assign({}, /*__GWOFY_CONFIG_JSON__*/);")


def test_validate_app_config_source_rejects_missing_assignment():
    with pytest.raises(ValueError, match="app_config_missing_gwofy_config"):
        validate_app_config_source("// empty")
    with pytest.raises(ValueError, match="app_config_missing_gwofy_config_assignment"):
        validate_app_config_source("var x = g.GWOFY_CONFIG;")

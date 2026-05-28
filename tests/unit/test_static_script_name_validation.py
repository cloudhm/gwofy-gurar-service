"""Static script filename validation."""

import pytest

from lib.static_scripts import script_name_rules, validate_script_name


def test_script_name_rules_documented():
    rules = script_name_rules()
    assert "app-config.js" in rules["reservedNames"]
    assert "store1.js" in rules["examplesValid"]
    assert "店铺.js" in rules["examplesInvalid"]


@pytest.mark.parametrize(
    "name",
    [
        "store1.js",
        "patch-v2.js",
        "app.storefront.js",
        "A1_b-2.js",
    ],
)
def test_valid_names(name):
    assert validate_script_name(name) == name


@pytest.mark.parametrize(
    "name,expected_code",
    [
        ("app-config.js", "script_name_reserved"),
        ("APP-CONFIG.JS", "script_name_reserved"),
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

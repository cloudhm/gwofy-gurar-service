import os

from lib.admin_cognito import admin_in_required_group, cognito_groups_from_claims


def test_parse_cognito_groups_json_string():
    claims = {"cognito:groups": '["GWOFY-SHIPPING-PROTECTION","Other"]'}
    g = cognito_groups_from_claims(claims)
    assert "GWOFY-SHIPPING-PROTECTION" in g


def test_parse_cognito_groups_bracket_string_not_json():
    """HTTP API sometimes forwards unquoted pseudo-array strings that json.loads rejects."""
    claims = {"cognito:groups": "[GWOFY-SHIPPING-PROTECTION]"}
    assert cognito_groups_from_claims(claims) == ["GWOFY-SHIPPING-PROTECTION"]


def test_parse_cognito_groups_bracket_string_two_groups():
    claims = {"cognito:groups": "[GWOFY-SHIPPING-PROTECTION, GWOFY-ADMIN]"}
    g = cognito_groups_from_claims(claims)
    assert "GWOFY-SHIPPING-PROTECTION" in g and "GWOFY-ADMIN" in g


def test_parse_cognito_groups_space_separated_string():
    claims = {"cognito:groups": "GWOFY-ADMIN GWOFY-SHIPPING-PROTECTION"}
    g = cognito_groups_from_claims(claims)
    assert "GWOFY-SHIPPING-PROTECTION" in g and "GWOFY-ADMIN" in g


def test_parse_cognito_groups_bracket_string_space_separated():
    claims = {"cognito:groups": "[GWOFY-ADMIN GWOFY-SHIPPING-PROTECTION]"}
    g = cognito_groups_from_claims(claims)
    assert "GWOFY-SHIPPING-PROTECTION" in g and "GWOFY-ADMIN" in g


def test_parse_cognito_groups_list_single_space_joined_element():
    claims = {"cognito:groups": ["GWOFY-ADMIN GWOFY-SHIPPING-PROTECTION"]}
    g = cognito_groups_from_claims(claims)
    assert "GWOFY-SHIPPING-PROTECTION" in g and "GWOFY-ADMIN" in g


def test_require_group_ok_with_multiple_groups():
    os.environ["ADMIN_COGNITO_GROUP"] = "GWOFY-SHIPPING-PROTECTION"
    ok, req = admin_in_required_group(
        {"cognito:groups": ["GWOFY-ADMIN GWOFY-SHIPPING-PROTECTION"], "sub": "x"}
    )
    assert ok and req == "GWOFY-SHIPPING-PROTECTION"


def test_parse_cognito_groups_list():
    claims = {"cognito:groups": ["GWOFY-SHIPPING-PROTECTION"]}
    assert cognito_groups_from_claims(claims) == ["GWOFY-SHIPPING-PROTECTION"]


def test_require_group_ok():
    os.environ["ADMIN_COGNITO_GROUP"] = "GWOFY-SHIPPING-PROTECTION"
    ok, req = admin_in_required_group(
        {"cognito:groups": ["GWOFY-SHIPPING-PROTECTION"], "sub": "x"}
    )
    assert ok and req == "GWOFY-SHIPPING-PROTECTION"


def test_require_group_fail():
    os.environ["ADMIN_COGNITO_GROUP"] = "GWOFY-SHIPPING-PROTECTION"
    ok, req = admin_in_required_group({"cognito:groups": ["Other"], "sub": "x"})
    assert not ok


def test_normalize_unicode_hyphen_matches_ascii_required():
    """Cognito strings sometimes use unicode dash; normalize to ASCII hyphen for comparison."""
    os.environ["ADMIN_COGNITO_GROUP"] = "GWOFY-SHIPPING-PROTECTION"
    # en-dash U+2013 between GWOFY and SHIPPING (example pathological case)
    weird = "GWOFY\u2013SHIPPING\u2013PROTECTION"
    ok, req = admin_in_required_group({"cognito:groups": [weird], "sub": "x"})
    assert ok and req == "GWOFY-SHIPPING-PROTECTION"


def test_strip_whitespace_on_groups_and_required():
    os.environ["ADMIN_COGNITO_GROUP"] = "  GWOFY-SHIPPING-PROTECTION  "
    ok, req = admin_in_required_group(
        {"cognito:groups": ["  GWOFY-SHIPPING-PROTECTION\t"], "sub": "x"}
    )
    assert ok and req == "GWOFY-SHIPPING-PROTECTION"

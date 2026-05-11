"""Shape of API Gateway HTTP API JWT authorizer → Lambda claims (debug session shapes)."""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")
os.environ.setdefault("TABLE_NAME", "dummy-table-for-import")

from admin_handler import _claims


def test_http_api_jwt_claims_colon_groups_string():
    ev = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "user1",
                        "cognito:groups": '["GWOFY-SHIPPING-PROTECTION"]',
                    }
                }
            }
        }
    }
    c = _claims(ev)
    assert c.get("sub") == "user1"
    assert "GWOFY-SHIPPING-PROTECTION" in str(c.get("cognito:groups"))


def test_http_api_jwt_claims_underscore_key():
    ev = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "user1",
                        "cognito_groups": '["GWOFY-SHIPPING-PROTECTION"]',
                    }
                }
            }
        }
    }
    c = _claims(ev)
    assert "cognito_groups" in c or "cognito:groups" in c

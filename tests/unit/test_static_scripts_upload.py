"""PUT /admin/static-scripts — JSON, raw JS body, base64, multipart."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("jwt", MagicMock())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://sp-prod.gwofy.com")


def _put_event(path: str, *, body: str, content_type: str, query: str | None = None):
    ev = {
        "requestContext": {
            "http": {"method": "PUT", "path": path},
            "requestId": "r1",
            "authorizer": {"jwt": {"claims": {"sub": "admin1"}}},
        },
        "headers": {"content-type": content_type},
        "body": body,
    }
    if query:
        ev["queryStringParameters"] = {"confirmOverwrite": query}
    return ev


def _mock_table():
    store: dict = {}

    def fake_put_item(Item, **_kwargs):
        store[(Item["pk"], Item["sk"])] = Item

    def fake_get_item(Key, **_kwargs):
        item = store.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    tbl = MagicMock()
    tbl.put_item.side_effect = fake_put_item
    tbl.get_item.side_effect = fake_get_item
    return tbl, store


def test_put_raw_javascript_body():
    from admin_handler import handler

    tbl, _ = _mock_table()
    js = "(function(){ window.STORE1 = true; })();\n"

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
    ):
        out = handler(
            _put_event(
                "/admin/static-scripts/store1.js",
                body=js,
                content_type="application/javascript",
            ),
            None,
        )

    assert out["statusCode"] == 201
    assert json.loads(out["body"])["source"] == js


def test_put_source_base64_json():
    from admin_handler import handler
    import base64

    tbl, _ = _mock_table()
    js = "console.log('from-base64');"
    b64 = base64.b64encode(js.encode()).decode()

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
    ):
        out = handler(
            _put_event(
                "/admin/static-scripts/store1.js",
                body=json.dumps({"sourceBase64": b64}),
                content_type="application/json",
            ),
            None,
        )

    assert out["statusCode"] == 201
    assert json.loads(out["body"])["source"] == js


def test_parse_static_script_put_payload_multipart():
    from lib.static_scripts import parse_static_script_put_payload

    boundary = "----BOUNDARY"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="store1.js"\r\n'
        "Content-Type: application/javascript\r\n\r\n"
        "var uploaded = 1;\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    event = {
        "headers": {"content-type": f"multipart/form-data; boundary={boundary}"},
        "body": body.decode("latin-1"),
        "queryStringParameters": {"confirmOverwrite": "true"},
    }
    source, confirm, is_app_config = parse_static_script_put_payload(event)
    assert "var uploaded = 1" in source
    assert confirm is True
    assert is_app_config is False


def test_parse_raw_js_is_app_config_from_query():
    from lib.static_scripts import parse_static_script_put_payload

    source = "g.GWOFY_CONFIG = Object.assign({}, /*__GWOFY_CONFIG_JSON__*/);"
    event = {
        "headers": {"content-type": "application/javascript"},
        "body": source,
        "queryStringParameters": {"isAppConfig": "true", "confirmOverwrite": "true"},
    }
    parsed_source, confirm, is_app_config = parse_static_script_put_payload(event)
    assert parsed_source == source
    assert confirm is True
    assert is_app_config is True


def test_put_raw_javascript_is_app_config_from_query():
    from admin_handler import handler

    tbl, store = _mock_table()
    js = "g.GWOFY_CONFIG = Object.assign({}, /*__GWOFY_CONFIG_JSON__*/);"

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
    ):
        out = handler(
            {
                "requestContext": {
                    "http": {"method": "PUT", "path": "/admin/static-scripts/app-config-z3.js"},
                    "requestId": "r1",
                    "authorizer": {"jwt": {"claims": {"sub": "admin1"}}},
                },
                "headers": {"content-type": "application/javascript"},
                "body": js,
                "queryStringParameters": {"isAppConfig": "true", "confirmOverwrite": "true"},
            },
            None,
        )

    assert out["statusCode"] == 201
    body = json.loads(out["body"])
    assert body["isAppConfig"] is True
    assert store[("GLOBAL#STATIC_JS", "app-config-z3.js")]["is_app_config"] is True

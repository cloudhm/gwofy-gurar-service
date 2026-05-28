"""Admin static-scripts: list, edit, upload, delete (Cognito admin only)."""

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


def _admin_event(method: str, path: str, body: dict | None = None):
    ev = {
        "requestContext": {
            "http": {"method": method, "path": path},
            "requestId": "r1",
            "authorizer": {"jwt": {"claims": {"sub": "admin1", "email": "a@ex.com"}}},
        },
        "headers": {},
    }
    if body is not None:
        ev["body"] = json.dumps(body)
    return ev


def test_list_static_scripts_empty():
    from admin_handler import handler

    tbl = MagicMock()
    tbl.query.return_value = {"Items": []}

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(_admin_event("GET", "/admin/static-scripts"), None)

    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["scripts"] == []
    assert "app-config.js" in body["nameRules"]["reservedNames"]


def test_put_create_and_get_script():
    from admin_handler import handler

    store: dict = {}

    def fake_put_item(Item, **_kwargs):
        store[(Item["pk"], Item["sk"])] = Item

    def fake_get_item(Key, **_kwargs):
        item = store.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    def fake_query(**_kwargs):
        return {"Items": list(store.values())}

    def fake_delete_item(Key, **_kwargs):
        store.pop((Key["pk"], Key["sk"]), None)

    tbl = MagicMock()
    tbl.put_item.side_effect = fake_put_item
    tbl.get_item.side_effect = fake_get_item
    tbl.query.side_effect = fake_query
    tbl.delete_item.side_effect = fake_delete_item

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
    ):
        create = handler(
            _admin_event(
                "PUT",
                "/admin/static-scripts/patch-v2.js",
                {"source": "var x = 1;", "confirmOverwrite": False},
            ),
            None,
        )
        assert create["statusCode"] == 201

        conflict = handler(
            _admin_event(
                "PUT",
                "/admin/static-scripts/patch-v2.js",
                {"source": "var x = 2;", "confirmOverwrite": False},
            ),
            None,
        )
        assert conflict["statusCode"] == 409
        assert json.loads(conflict["body"])["error"] == "name_exists"

        overwrite = handler(
            _admin_event(
                "PUT",
                "/admin/static-scripts/patch-v2.js",
                {"source": "var x = 2;", "confirmOverwrite": True},
            ),
            None,
        )
        assert overwrite["statusCode"] == 200

        got = handler(_admin_event("GET", "/admin/static-scripts/patch-v2.js"), None)
        assert got["statusCode"] == 200
        assert json.loads(got["body"])["source"] == "var x = 2;"


def test_delete_static_script():
    from admin_handler import handler

    store: dict = {}

    def fake_put_item(Item, **_kwargs):
        store[(Item["pk"], Item["sk"])] = Item

    def fake_get_item(Key, **_kwargs):
        item = store.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    tbl = MagicMock()
    tbl.put_item.side_effect = fake_put_item
    tbl.get_item.side_effect = fake_get_item
    tbl.delete_item.side_effect = lambda Key, **_kw: store.pop((Key["pk"], Key["sk"]), None)

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
    ):
        handler(
            _admin_event(
                "PUT",
                "/admin/static-scripts/temp.js",
                {"source": "// t", "confirmOverwrite": True},
            ),
            None,
        )
        out = handler(_admin_event("DELETE", "/admin/static-scripts/temp.js"), None)
    assert out["statusCode"] == 200


def test_put_reserved_app_config_js_rejected():
    from admin_handler import handler

    tbl = MagicMock()
    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(
            {
                "requestContext": {
                    "http": {"method": "PUT", "path": "/admin/static-scripts/app-config.js"},
                    "requestId": "r1",
                    "authorizer": {"jwt": {"claims": {"sub": "admin1"}}},
                },
                "headers": {"content-type": "application/json"},
                "body": json.dumps({"source": "// x", "confirmOverwrite": False}),
            },
            None,
        )
    assert out["statusCode"] == 400
    body = json.loads(out["body"])
    assert body["error"] == "invalid_script_name"
    assert body["detail"] == "script_name_reserved"


def test_non_admin_forbidden():
    from admin_handler import handler

    with patch("admin_handler.admin_in_required_group", return_value=(False, "GWOFY-SHIPPING-PROTECTION")):
        out = handler(_admin_event("GET", "/admin/static-scripts"), None)
    assert out["statusCode"] == 403

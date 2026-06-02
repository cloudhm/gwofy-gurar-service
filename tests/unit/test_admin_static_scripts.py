"""Admin static-scripts: list, edit, upload, delete (Cognito admin only)."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("jwt", MagicMock())

_VALID_APP_CONFIG_SOURCE = (
    "g.GWOFY_CONFIG = Object.assign({ styles: {} }, /*__GWOFY_CONFIG_JSON__*/);"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-east-1")
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://sp-prod.gwofy.com")


def _admin_event(method: str, path: str, body: dict | None = None, query: str | None = None):
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
    if query is not None:
        ev["queryStringParameters"] = {"isAppConfig": query}
    return ev


def _mock_script_table():
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
    return tbl, store


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
    assert "app-config.js" in body["nameRules"]["examplesValid"]


def test_list_static_scripts_filter_is_app_config():
    from admin_handler import handler

    tbl = MagicMock()
    tbl.query.return_value = {
        "Items": [
            {
                "pk": "GLOBAL#STATIC_JS",
                "sk": "patch.js",
                "is_app_config": False,
                "updated_at": "t",
                "updated_by": "a",
                "byte_length": 1,
            },
            {
                "pk": "GLOBAL#STATIC_JS",
                "sk": "app-config-v1.js",
                "is_app_config": True,
                "updated_at": "t",
                "updated_by": "a",
                "byte_length": 2,
            },
        ]
    }

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(_admin_event("GET", "/admin/static-scripts", query="true"), None)

    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["isAppConfigFilter"] is True
    assert len(body["scripts"]) == 1
    assert body["scripts"][0]["name"] == "app-config-v1.js"
    assert body["scripts"][0]["isAppConfig"] is True


def test_list_static_scripts_invalid_is_app_config_query():
    from admin_handler import handler

    tbl = MagicMock()
    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(_admin_event("GET", "/admin/static-scripts", query="maybe"), None)

    assert out["statusCode"] == 400
    assert json.loads(out["body"])["error"] == "invalid_isAppConfig_query"


def test_put_create_and_get_script():
    from admin_handler import handler

    tbl, _store = _mock_script_table()

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
        assert json.loads(create["body"])["isAppConfig"] is False

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

    tbl, _store = _mock_script_table()

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


def test_put_app_config_js_allowed_with_flag():
    from admin_handler import handler

    tbl, _store = _mock_script_table()

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
        patch("admin_handler.append_audit"),
    ):
        out = handler(
            _admin_event(
                "PUT",
                "/admin/static-scripts/app-config.js",
                {
                    "source": _VALID_APP_CONFIG_SOURCE,
                    "confirmOverwrite": True,
                    "isAppConfig": True,
                },
            ),
            None,
        )
    assert out["statusCode"] == 201
    body = json.loads(out["body"])
    assert body["isAppConfig"] is True
    assert body["name"] == "app-config.js"


def test_put_app_config_missing_gwofy_config_rejected():
    from admin_handler import handler

    tbl = MagicMock()
    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(
            _admin_event(
                "PUT",
                "/admin/static-scripts/app-config.js",
                {
                    "source": "var x = 1;",
                    "confirmOverwrite": False,
                    "isAppConfig": True,
                },
            ),
            None,
        )
    assert out["statusCode"] == 400
    body = json.loads(out["body"])
    assert body["error"] == "invalid_app_config"
    assert body["detail"] == "app_config_missing_gwofy_config"


def test_delete_app_config_script_in_use_rejected():
    from admin_handler import handler

    tbl, store = _mock_script_table()
    store[("GLOBAL#STATIC_JS", "bound.js")] = {
        "pk": "GLOBAL#STATIC_JS",
        "sk": "bound.js",
        "source": _VALID_APP_CONFIG_SOURCE,
        "is_app_config": True,
        "byte_length": 1,
        "content_sha256": "x",
        "updated_at": "t",
        "updated_by": "admin",
    }

    def fake_query(**kwargs):
        if kwargs.get("IndexName") == "GSI2":
            return {
                "Items": [
                    {
                        "pk": "SHOP#gwo-dev.myshopify.com",
                        "sk": "METADATA",
                        "shop": "gwo-dev.myshopify.com",
                        "app_config_script_name": "bound.js",
                    }
                ]
            }
        return {"Items": list(store.values())}

    tbl.query.side_effect = fake_query

    with (
        patch("admin_handler.admin_in_required_group", return_value=(True, "GWOFY-SHIPPING-PROTECTION")),
        patch("admin_handler.ddb.Table", return_value=tbl),
    ):
        out = handler(_admin_event("DELETE", "/admin/static-scripts/bound.js"), None)

    assert out["statusCode"] == 409
    body = json.loads(out["body"])
    assert body["error"] == "app_config_script_in_use"
    assert "gwo-dev.myshopify.com" in body["boundShops"]


def test_non_admin_forbidden():
    from admin_handler import handler

    with patch("admin_handler.admin_in_required_group", return_value=(False, "GWOFY-SHIPPING-PROTECTION")):
        out = handler(_admin_event("GET", "/admin/static-scripts"), None)
    assert out["statusCode"] == 403

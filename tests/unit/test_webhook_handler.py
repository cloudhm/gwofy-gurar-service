import json
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from webhook_handler import _request_snapshot, _utf8_body_for_log, handler
from lib.lambda_warmup import WARMUP_EVENT_SOURCE


def test_webhook_handler_warmup_returns_ok_without_side_effects(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("WORK_QUEUE_URL", "https://example.com/queue")

    def _fail_send(*_a, **_k):
        raise AssertionError("sqs should not be called on warmup")

    monkeypatch.setattr("webhook_handler.sqs.send_message", _fail_send)

    out = handler({"source": WARMUP_EVENT_SOURCE}, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["ok"] is True
    assert body["warmup"] is True


def test_utf8_body_for_log_short_body():
    text, truncated = _utf8_body_for_log(b'{"id":1}')
    assert text == '{"id":1}'
    assert truncated is False


def test_utf8_body_for_log_truncates_long_body():
    raw = b"x" * 250_000
    text, truncated = _utf8_body_for_log(raw)
    assert truncated is True
    assert len(text) == 200_000


def test_utf8_body_for_log_invalid_utf8():
    text, truncated = _utf8_body_for_log(b"\xff\xfe")
    assert truncated is False
    assert len(text) == 2
    assert text == "\ufffd\ufffd"


def test_request_snapshot_rest_proxy_event():
    event = {
        "httpMethod": "POST",
        "path": "/webhook",
        "resource": "/webhook",
        "headers": {"X-Shopify-Topic": "orders/create", "Host": "example.amazonaws.com"},
        "multiValueHeaders": {"X-Foo": ["a", "b"]},
        "queryStringParameters": {"foo": "bar"},
        "multiValueQueryStringParameters": {"tag": ["x", "y"]},
        "body": '{"id":1}',
        "isBase64Encoded": False,
        "requestContext": {"stage": "prod"},
    }
    snap = _request_snapshot(event, b'{"id":1}')
    assert snap["http_method"] == "POST"
    assert snap["path"] == "/webhook"
    assert snap["resource"] == "/webhook"
    assert snap["stage"] == "prod"
    assert snap["query_string_parameters"] == {"foo": "bar"}
    assert snap["multi_value_query_string_parameters"] == {"tag": ["x", "y"]}
    assert snap["headers"]["X-Shopify-Topic"] == "orders/create"
    assert snap["multi_value_headers"]["X-Foo"] == ["a", "b"]
    assert snap["body_utf8"] == '{"id":1}'
    assert snap["body_truncated"] is False
    assert snap["body_len_bytes"] == 8


def test_request_snapshot_http_api_v2_style():
    event = {
        "version": "2.0",
        "rawPath": "/hook",
        "rawQueryString": "a=1&b=2",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-shopify-topic": "app/uninstalled"},
        "body": "e30=",
        "isBase64Encoded": True,
    }
    snap = _request_snapshot(event, b"{}")
    assert snap["event_version"] == "2.0"
    assert snap["http_method"] == "POST"
    assert snap["path"] == "/hook"
    assert snap["raw_query_string"] == "a=1&b=2"
    assert snap["is_base64_encoded"] is True
    assert snap["body_utf8"] == "{}"

"""Shopify webhook ingress — HMAC only, enqueue full payload to SQS."""

from __future__ import annotations

import base64
import json
import os

import boto3

from lib.logging_json import setup_logging
from lib.shopify_api import verify_webhook_hmac

logger = setup_logging("webhook_ingress")
sqs = boto3.client("sqs")

# CloudWatch log event limit is 256 KiB; keep headroom for JSON/metadata.
_MAX_HMAC_FAIL_BODY_CHARS = 200_000
_MAX_HEADER_VALUE_CHARS = 16_384


def _utf8_body_for_log(raw_body: bytes) -> tuple[str, bool]:
    text = raw_body.decode("utf-8", errors="replace")
    if len(text) <= _MAX_HMAC_FAIL_BODY_CHARS:
        return text, False
    return text[: _MAX_HMAC_FAIL_BODY_CHARS], True


def _truncate_header_value(value: object) -> object:
    if isinstance(value, list):
        return [_truncate_header_value(v) for v in value]
    s = value if isinstance(value, str) else str(value)
    if len(s) <= _MAX_HEADER_VALUE_CHARS:
        return s
    return s[:_MAX_HEADER_VALUE_CHARS]


def _request_snapshot(event: dict, raw_body: bytes) -> dict:
    """API Gateway REST/Lambda proxy or HTTP API v2 shaped events — for CloudWatch only."""
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    body_text, body_truncated = _utf8_body_for_log(raw_body)
    headers_in = event.get("headers") or {}
    mv_headers_in = event.get("multiValueHeaders") or {}
    return {
        "event_version": event.get("version"),
        "http_method": event.get("httpMethod") or http.get("method") or "",
        "path": event.get("path") or event.get("rawPath") or "",
        "resource": event.get("resource") or "",
        "stage": rc.get("stage") or "",
        "raw_query_string": event.get("rawQueryString") or "",
        "query_string_parameters": event.get("queryStringParameters") or {},
        "multi_value_query_string_parameters": event.get("multiValueQueryStringParameters")
        or {},
        "headers": {k: _truncate_header_value(v) for k, v in headers_in.items()},
        "multi_value_headers": {
            k: _truncate_header_value(v) for k, v in mv_headers_in.items()
        },
        "is_base64_encoded": bool(event.get("isBase64Encoded")),
        "body_len_bytes": len(raw_body),
        "body_utf8": body_text,
        "body_truncated": body_truncated,
    }


def handler(event, context):
    client_secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    queue_url = os.environ["WORK_QUEUE_URL"]

    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw)
    elif isinstance(raw, str):
        raw_body = raw.encode("utf-8")
    else:
        raw_body = raw

    logger.info(
        "webhook_request",
        extra={"request": _request_snapshot(event, raw_body)},
    )

    headers_in = event.get("headers") or {}
    headers = {}
    for k, v in headers_in.items():
        headers[k.lower()] = v

    hmac_header = headers.get("x-shopify-hmac-sha256") or ""
    if not verify_webhook_hmac(raw_body, hmac_header, client_secret):
        body_text, truncated = _utf8_body_for_log(raw_body)
        logger.warning(
            "webhook_hmac_failed",
            extra={
                "topic": headers.get("x-shopify-topic") or "",
                "shop": headers.get("x-shopify-shop-domain") or "",
                "payload_len": len(raw_body),
                "raw_body": body_text,
                "raw_body_truncated": truncated,
            },
        )
        return {"statusCode": 200, "body": json.dumps({"ok": True})}

    needed = [
        "x-shopify-topic",
        "x-shopify-webhook-id",
        "x-shopify-shop-domain",
        "x-shopify-api-version",
        "x-shopify-triggered-at",
        "x-shopify-event-id",
    ]
    out_headers = {k: headers.get(k, "") for k in needed}
    # Preserve any extra Shopify headers if present
    for k, v in headers.items():
        if k.startswith("x-shopify-") and k not in out_headers:
            out_headers[k] = v

    envelope = {
        "source": "webhook_ingress",
        "headers": out_headers,
        "body": raw_body.decode("utf-8", errors="replace"),
    }

    try:
        send_resp = sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(envelope))
    except Exception:
        logger.exception(
            "sqs_send_failed",
            extra={
                "topic": out_headers.get("x-shopify-topic"),
                "shop": out_headers.get("x-shopify-shop-domain"),
                "webhook_id": out_headers.get("x-shopify-webhook-id"),
            },
        )
        return {"statusCode": 503, "body": json.dumps({"error": "queue_unavailable"})}

    logger.info(
        "webhook_enqueued",
        extra={
            "topic": out_headers.get("x-shopify-topic"),
            "shop": out_headers.get("x-shopify-shop-domain"),
            "webhook_id": out_headers.get("x-shopify-webhook-id"),
            "payload_len": len(raw_body),
            "sqs_message_id": send_resp.get("MessageId"),
        },
    )
    return {"statusCode": 200, "body": json.dumps({"ok": True})}

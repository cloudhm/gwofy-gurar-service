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

    headers_in = event.get("headers") or {}
    headers = {}
    for k, v in headers_in.items():
        headers[k.lower()] = v

    hmac_header = headers.get("x-shopify-hmac-sha256") or ""
    if not verify_webhook_hmac(raw_body, hmac_header, client_secret):
        logger.warning("webhook_hmac_failed", extra={"payload_len": len(raw_body)})
        return {"statusCode": 401, "body": json.dumps({"error": "invalid_hmac"})}

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
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(envelope))
    except Exception:
        logger.exception("sqs_send_failed")
        return {"statusCode": 503, "body": json.dumps({"error": "queue_unavailable"})}

    logger.info(
        "webhook_enqueued",
        extra={
            "topic": out_headers.get("x-shopify-topic"),
            "shop": out_headers.get("x-shopify-shop-domain"),
            "webhook_id": out_headers.get("x-shopify-webhook-id"),
            "payload_len": len(raw_body),
        },
    )
    return {"statusCode": 200, "body": json.dumps({"ok": True})}

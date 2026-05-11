"""Shopify OAuth callback — token exchange, KMS, store_number, SQS fan-out."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import boto3

from lib.kms_tokens import encrypt_token
from lib.logging_json import setup_logging
from lib.models import SK_METADATA, pk_shop
from lib.shopify_api import DEFAULT_API_VERSION, exchange_token, register_webhook_rest, verify_oauth_hmac
from lib.store_number import allocate_store_number

logger = setup_logging("oauth")

ddb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    # Also support body for POST callbacks if configured
    qs = {k: v[0] if isinstance(v, list) else v for k, v in params.items()}

    shop = (qs.get("shop") or "").strip().lower().rstrip("/")
    code = qs.get("code")
    state = qs.get("state", "")
    if not shop or not code:
        return _resp(400, {"error": "missing_shop_or_code"})

    client_id = os.environ["SHOPIFY_CLIENT_ID"]
    client_secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    kms_key_id = os.environ["KMS_KEY_ID"]
    table_name = os.environ["TABLE_NAME"]
    queue_url = os.environ["WORK_QUEUE_URL"]
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)

    if not verify_oauth_hmac(qs, client_secret):
        logger.warning("oauth_hmac_failed", extra={"shop": shop})
        return _resp(403, {"error": "invalid_hmac"})

    try:
        token_resp = exchange_token(shop, client_id, client_secret, code)
    except Exception as e:
        logger.exception("token_exchange_failed")
        return _resp(502, {"error": "token_exchange_failed", "detail": str(e)})

    access_token = token_resp.get("access_token") or ""
    scopes = token_resp.get("scope") or ""

    store_number = allocate_store_number(table_name, shop)
    enc = encrypt_token(kms_key_id, access_token)

    table = ddb.Table(table_name)
    pk = pk_shop(shop)
    now = datetime.now(timezone.utc).isoformat()

    table.put_item(
        Item={
            "pk": pk,
            "sk": SK_METADATA,
            "shop": shop,
            "store_number": store_number,
            "access_token_enc": enc,
            "scopes": scopes,
            "installation_status": "ACTIVE",
            "installed_at": now,
            "updated_at": now,
            "kms_key_id": kms_key_id,
            "oauth_state_last": state,
        }
    )

    webhook_base = os.environ.get("WEBHOOK_BASE_URL", "").rstrip("/")
    if webhook_base:
        hook_url = f"{webhook_base}/webhooks/shopify"
        topics = [
            "app/uninstalled",
            "products/create",
            "products/update",
            "orders/create",
            "orders/updated",
            "customers/create",
            "customers/update",
            "customers/data_request",
            "customers/redact",
            "shop/redact",
        ]
        for topic in topics:
            try:
                register_webhook_rest(shop, access_token, topic, hook_url, api_version=api_version)
            except Exception as e:
                logger.warning(
                    "webhook_register_failed",
                    extra={"topic": topic, "shop": shop, "error": str(e)[:200]},
                )

    internal = {
        "source": "oauth",
        "shop": shop,
        "store_number": store_number,
        "api_version": api_version,
    }
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({**internal, "event": "INITIAL_SYNC"}),
    )
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({**internal, "event": "APP_INSTALLED"}),
    )

    redirect = os.environ.get("POST_INSTALL_REDIRECT_URL")
    if redirect:
        return {"statusCode": 302, "headers": {"Location": redirect}, "body": ""}

    return _resp(
        200,
        {
            "ok": True,
            "shop": shop,
            "store_number": store_number,
            "message": "Installed; sync queued.",
        },
    )


def _resp(code: int, body: dict):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }

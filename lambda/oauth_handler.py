"""Shopify OAuth callback — token exchange, KMS, store_number, SQS fan-out."""

from __future__ import annotations

import json
import os

import boto3

from lib.logging_json import setup_logging
from lib.shop_install import enqueue_install_worker_jobs, upsert_shop_metadata_from_offline_tokens
from lib.shopify_api import DEFAULT_API_VERSION, exchange_token, verify_oauth_hmac

logger = setup_logging("oauth")

ddb = boto3.resource("dynamodb")


def _post_install_redirect_location(shop: str, app_client_id: str) -> str:
    custom = (os.environ.get("POST_INSTALL_REDIRECT_URL") or "").strip()
    if custom:
        return custom
    host = shop.strip().lower().rstrip("/")
    return f"https://{host}/admin/apps/{app_client_id}"


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

    rt = token_resp.get("refresh_token")
    exp_in = int(token_resp.get("expires_in") or 0)
    rt_exp_in = int(token_resp.get("refresh_token_expires_in") or 0)
    if not (isinstance(rt, str) and rt.strip() and exp_in > 0 and rt_exp_in > 0):
        logger.warning(
            "oauth_token_response_missing_expiring_fields",
            extra={"shop": shop, "keys": list(token_resp.keys())},
        )

    table = ddb.Table(table_name)
    store_number = upsert_shop_metadata_from_offline_tokens(
        table,
        table_name,
        shop,
        token_resp,
        kms_key_id,
        oauth_state_last=state,
    )

    # Webhook topics are declared in shopify.app.toml (app config). Do not register
    # the same topics again via Admin REST here — that duplicates subscriptions when
    # both TOML and OAuth run, causing multiple deliveries per Shopify event.

    msg_ids = enqueue_install_worker_jobs(
        queue_url=queue_url,
        shop=shop,
        store_number=store_number,
        api_version=api_version,
        source="oauth",
    )
    logger.info(
        "oauth_work_queue_enqueued",
        extra={
            "shop": shop,
            "store_number": store_number,
            "initial_sync_sqs_message_id": msg_ids.get("initial_sync_message_id"),
            "app_installed_sqs_message_id": msg_ids.get("app_installed_message_id"),
        },
    )

    location = _post_install_redirect_location(shop, client_id)
    return {"statusCode": 302, "headers": {"Location": location}, "body": ""}


def _resp(code: int, body: dict):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }

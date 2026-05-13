"""Shopify OAuth callback — token exchange, KMS, store_number, SQS fan-out."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3

from lib.kms_tokens import encrypt_token
from lib.logging_json import setup_logging
from lib.models import GSI2_PK_SHOP_INDEX, SK_METADATA, pk_shop
from lib.shopify_api import DEFAULT_API_VERSION, exchange_token, verify_oauth_hmac
from lib.store_number import allocate_store_number

logger = setup_logging("oauth")

ddb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")


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

    access_token = token_resp.get("access_token") or ""
    scopes = token_resp.get("scope") or ""

    store_number = allocate_store_number(table_name, shop)
    enc = encrypt_token(kms_key_id, access_token)

    table = ddb.Table(table_name)
    pk = pk_shop(shop)
    now = datetime.now(timezone.utc).isoformat()
    prev = table.get_item(Key={"pk": pk, "sk": SK_METADATA}).get("Item") or {}
    installed_at = str(prev.get("installed_at") or now)

    item = {
        "pk": pk,
        "sk": SK_METADATA,
        "shop": shop,
        "store_number": store_number,
        "access_token_enc": enc,
        "scopes": scopes,
        "installation_status": "ACTIVE",
        "installed_at": installed_at,
        "updated_at": now,
        "kms_key_id": kms_key_id,
        "oauth_state_last": state,
        "activation_status": "UNACTIVATED",
        "return_insurance_status": "CLOSED",
        "shipping_protection_status": "CLOSED",
        "plugin_suspended": False,
        "embed_enabled_ack": False,
        "gsi2pk": GSI2_PK_SHOP_INDEX,
        "gsi2sk": f"{installed_at}#{shop}",
    }
    for k in (
        "activation_status",
        "protection_product_gid",
        "embed_enabled_ack",
        "return_insurance_status",
        "shipping_protection_status",
        "plugin_suspended",
        "sp_below_min_coverage_tip",
        "sp_greater_max_coverage_tip",
    ):
        if prev.get(k) is not None:
            item[k] = prev[k]

    table.put_item(Item=item)

    # Webhook topics are declared in shopify.app.toml (app config). Do not register
    # the same topics again via Admin REST here — that duplicates subscriptions when
    # both TOML and OAuth run, causing multiple deliveries per Shopify event.

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

    location = _post_install_redirect_location(shop, client_id)
    return {"statusCode": 302, "headers": {"Location": location}, "body": ""}


def _resp(code: int, body: dict):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }

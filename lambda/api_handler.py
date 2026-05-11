"""Embedded app backend — verify Session Token JWT, return shop metadata from DynamoDB."""

from __future__ import annotations

import json
import os

import boto3

from lib.logging_json import setup_logging
from lib.models import SK_METADATA, pk_shop
from lib.session_jwt import shop_host_from_payload, verify_session_token

logger = setup_logging("api")

ddb = boto3.resource("dynamodb")


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        return _resp(401, {"error": "missing_bearer"})
    token = auth.split(" ", 1)[1].strip()
    api_key = os.environ["SHOPIFY_CLIENT_ID"]
    api_secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    table_name = os.environ["TABLE_NAME"]

    try:
        payload = verify_session_token(token, api_key, api_secret)
    except Exception as e:
        logger.info("session_token_invalid", extra={"detail": str(e)[:120]})
        return _resp(401, {"error": "invalid_session_token"})

    shop_host = shop_host_from_payload(payload)
    if not shop_host:
        return _resp(400, {"error": "cannot_resolve_shop"})

    table = ddb.Table(table_name)
    item = table.get_item(Key={"pk": pk_shop(shop_host), "sk": SK_METADATA}).get("Item")
    if not item:
        return _resp(404, {"error": "shop_not_installed"})

    safe = {
        "shop": item.get("shop"),
        "store_number": item.get("store_number"),
        "installation_status": item.get("installation_status"),
        "scopes": item.get("scopes"),
        "installed_at": item.get("installed_at"),
        "updated_at": item.get("updated_at"),
    }
    return _resp(200, {"session": payload.get("sub"), "shop_metadata": safe})


def _resp(code: int, body: dict):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }

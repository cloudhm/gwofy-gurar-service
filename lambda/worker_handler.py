"""SQS worker — internal jobs + Shopify webhooks (async only)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3

from lib.customer_order_sync import sync_orders
from lib.feishu import send_text
from lib.kms_tokens import decrypt_token
from lib.logging_json import setup_logging
from lib.models import SK_METADATA, pk_shop, pk_tenant
from lib.shop_archive import archive_and_delete_shop
from lib.activity_config import ensure_activity_info_seed
from lib.calc_coverage_tips_config import ensure_calc_coverage_tips_seed
from lib.tips_config import ensure_tips_info_seed
from lib.pricing_config import ensure_default_pricing_seed
from lib.max_coverage_config import ensure_max_coverage_seed
from lib.shipping_country_defaults import ensure_shipping_country_defaults_seed
from lib.product_sync import sync_products_initial
from lib.shop_profile_sync import sync_shop_profile
from lib.shopify_api import DEFAULT_API_VERSION, graphql_request
from lib.order_protection import order_has_protection_product
from lib.sync_order_tags import order_sync_tags

logger = setup_logging("worker")

ddb = boto3.resource("dynamodb")
kms_key_id_env = None


def handler(event, context):
    table_name = os.environ["TABLE_NAME"]
    kms_key_id = os.environ["KMS_KEY_ID"]
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    feishu_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    table = ddb.Table(table_name)

    for record in event.get("Records", []):
        body = json.loads(record["body"])
        try:
            route_message(table, body, kms_key_id, api_version, feishu_url)
        except Exception:
            logger.exception("worker_record_failed", extra={"body_preview": json.dumps(body)[:300]})
            raise


def route_message(table, body: dict[str, Any], kms_key_id: str, api_version: str, feishu_url: str) -> None:
    src = body.get("source")
    if src == "oauth":
        ev = body.get("event")
        shop = body["shop"]
        store_number = body["store_number"]
        if ev == "INITIAL_SYNC":
            run_initial_sync(table, shop, store_number, kms_key_id, api_version)
        elif ev == "APP_INSTALLED":
            send_text(
                feishu_url,
                f"[Gwofy] App installed\nshop={shop}\nstore_number={store_number}\n"
                f"time={datetime.now(timezone.utc).isoformat()}",
            )
        return

    if src == "merchant_api":
        ev = body.get("event")
        shop = body["shop"]
        store_number = str(body.get("store_number", ""))
        meta = _shop_row(table, shop)
        if not meta or meta.get("installation_status") != "ACTIVE":
            logger.info("skip_merchant_job_inactive", extra={"shop": shop, "event": ev})
            return
        enc = meta.get("access_token_enc")
        if not enc:
            return
        key_id = meta.get("kms_key_id") or kms_key_id
        token = decrypt_token(key_id, enc)
        ver = body.get("api_version") or api_version
        if ev == "SHOP_PROFILE_SYNC":
            sync_shop_profile(table, shop, token, ver)
            return
        logger.warning("unknown_merchant_event", extra={"event": ev})
        return

    if src == "reconcile":
        shop = body["shop"]
        store_number = body["store_number"]
        run_initial_sync(table, shop, store_number, kms_key_id, api_version)
        _advance_reconcile_marker(table, shop, body.get("resource", "ALL"))
        return

    if src == "webhook_ingress":
        process_webhook_envelope(table, body, kms_key_id, api_version, feishu_url)
        return

    logger.warning("unknown_message_source", extra={"source": src})


def _shop_row(table, shop: str) -> dict[str, Any] | None:
    return table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")


def run_initial_sync(table, shop: str, store_number: str, kms_key_id: str, api_version: str) -> None:
    meta = _shop_row(table, shop)
    if not meta or meta.get("installation_status") != "ACTIVE":
        logger.info("skip_sync_not_active", extra={"shop": shop})
        return
    enc = meta.get("access_token_enc")
    if not enc:
        logger.warning("skip_sync_no_token", extra={"shop": shop})
        return
    key_id = meta.get("kms_key_id") or kms_key_id
    token = decrypt_token(key_id, enc)
    ensure_default_pricing_seed(os.environ["TABLE_NAME"])
    ensure_shipping_country_defaults_seed(os.environ["TABLE_NAME"])
    ensure_max_coverage_seed(os.environ["TABLE_NAME"])
    ensure_activity_info_seed(os.environ["TABLE_NAME"])
    ensure_tips_info_seed(os.environ["TABLE_NAME"])
    ensure_calc_coverage_tips_seed(os.environ["TABLE_NAME"])
    sync_shop_profile(table, shop, token, api_version)
    meta = _shop_row(table, shop) or {}
    protection_gid = meta.get("protection_product_gid")
    sync_products_initial(table, shop, store_number, token, api_version)
    sync_orders(
        table, shop, store_number, token, api_version, protection_product_gid=protection_gid
    )


def _advance_reconcile_marker(table, shop: str, resource: str) -> None:
    shop_norm = shop.strip().lower().rstrip("/")
    pk = f"SYNC#{shop_norm}"
    sk = f"RECONCILE#{resource}"
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(Item={"pk": pk, "sk": sk, "last_reconciled_at": now})


def process_webhook_envelope(
    table,
    envelope: dict[str, Any],
    kms_key_id: str,
    api_version: str,
    feishu_url: str,
) -> None:
    headers = {k.lower(): v for k, v in (envelope.get("headers") or {}).items()}
    topic = headers.get("x-shopify-topic") or ""
    webhook_id = headers.get("x-shopify-webhook-id") or ""
    shop = (headers.get("x-shopify-shop-domain") or "").strip()
    t = (topic or "").lower().strip()

    if t == "app/uninstalled":
        handle_uninstall(table, shop, feishu_url, webhook_id)
        return

    if t in ("customers/data_request", "customers/redact", "shop/redact"):
        handle_gdpr(table, shop, t, envelope.get("body") or "")
        return

    meta = _shop_row(table, shop)
    if not meta or meta.get("installation_status") != "ACTIVE":
        logger.info("skip_webhook_shop_not_active", extra={"shop": shop, "topic": topic})
        return

    enc = meta.get("access_token_enc")
    store_number = str(meta.get("store_number", ""))
    if not enc:
        return
    key_id = meta.get("kms_key_id") or kms_key_id
    token = decrypt_token(key_id, enc)

    if t in ("shop/update", "markets/create", "markets/update"):
        try:
            sync_shop_profile(table, shop, token, api_version)
        except Exception:
            logger.exception("shop_profile_webhook_failed", extra={"shop": shop, "topic": t})
        return

    if t in ("products/update", "products/create"):
        handle_product_webhook(table, shop, store_number, token, envelope.get("body") or "", api_version)
    elif t in ("orders/create", "orders/updated"):
        handle_order_pull_webhook(table, shop, store_number, token, envelope.get("body") or "", api_version)


def handle_uninstall(table, shop: str, feishu_url: str, webhook_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    archived_name = os.environ.get("ARCHIVED_TABLE_NAME", "").strip()
    if archived_name:
        archive_table = ddb.Table(archived_name)
        n = archive_and_delete_shop(table, archive_table, shop)
        logger.info("shop_archived_on_uninstall", extra={"shop": shop, "rows": n})
    else:
        logger.error("missing_archived_table_name", extra={"shop": shop})
    send_text(
        feishu_url,
        f"[Gwofy] App uninstalled\nshop={shop}\nwebhook_id={webhook_id}\ntime={now}",
    )


def handle_gdpr(table, shop: str, topic: str, raw_body: str) -> None:
    """Mandatory compliance webhooks — persist audit row (§compliance)."""
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload = {}
    meta = _shop_row(table, shop) or {}
    sn = str(meta.get("store_number") or "unknown")
    pk_t = pk_tenant(sn)
    rid = str(payload.get("id") or payload.get("customer") or "") or _payload_hash_id(payload)
    sk = f"GDPR#{topic}#{rid}"
    table.put_item(
        Item={
            "pk": pk_t,
            "sk": sk,
            "topic": topic,
            "payload_json": raw_body[:35000],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "shop": shop,
        }
    )


def _payload_hash_id(payload: dict[str, Any]) -> str:
    return str(abs(hash(json.dumps(payload, sort_keys=True))))[:18]


ONE_PRODUCT_Q = """
query OneProduct($id: ID!) {
  product(id: $id) {
    id
    updatedAt
    metafields(first: 50) {
      edges { node { namespace key type value updatedAt } }
    }
    variants(first: 50) {
      edges {
        node {
          id
          updatedAt
          price
          compareAtPrice
          metafields(first: 30) {
            edges { node { namespace key type value updatedAt } }
          }
        }
      }
    }
  }
}
"""


def handle_product_webhook(table, shop: str, store_number: str, token: str, raw_body: str, api_version: str) -> None:
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return
    pid = body.get("admin_graphql_api_id")
    if not pid and body.get("id") is not None:
        pid = f"gid://shopify/Product/{body['id']}"
    if not pid:
        return
    data = graphql_request(shop, token, ONE_PRODUCT_Q, {"id": pid}, api_version=api_version)
    if data.get("errors"):
        logger.warning("product_fetch_errors", extra={"errors": str(data["errors"])[:500]})
        return
    node = data.get("data", {}).get("product")
    if not node:
        return
    from lib import product_sync as ps

    gid = node["id"]
    snap = {
        "gid": gid,
        "updatedAt": node.get("updatedAt"),
        "variants": [],
        "product_metafields": ps._flatten_metafields((node.get("metafields") or {}).get("edges") or []),
    }
    for ve in (node.get("variants") or {}).get("edges") or []:
        vn = ve["node"]
        snap["variants"].append(
            {
                "id": vn["id"],
                "price": vn.get("price"),
                "compareAtPrice": vn.get("compareAtPrice"),
                "updatedAt": vn.get("updatedAt"),
                "metafields": ps._flatten_metafields((vn.get("metafields") or {}).get("edges") or []),
            }
        )
    h = ps._snapshot_hash(snap)
    pk_t = pk_tenant(store_number)
    sk_p = f"PRODUCT#{gid}"
    prev = table.get_item(Key={"pk": pk_t, "sk": sk_p}).get("Item")
    prev_hash = (prev or {}).get("snapshot_hash")
    now = datetime.now(timezone.utc).isoformat()
    if prev_hash != h:
        ps._write_history_if_changed(table, pk_t, store_number, prev, snap, now)
    table.put_item(
        Item={
            "pk": pk_t,
            "sk": sk_p,
            "payload": json.dumps(snap, default=str),
            "snapshot_hash": h,
            "updated_at_source": node.get("updatedAt"),
            "synced_at": now,
            "shopify_id": gid,
        }
    )


ORDER_ONE_Q = """
query OrderOne($id: ID!) {
  order(id: $id) {
    id
    legacyResourceId
    updatedAt
    name
    processedAt
    createdAt
    lineItems(first: 50) {
      edges {
        node {
          quantity
          product { id }
        }
      }
    }
  }
}
"""


def handle_order_pull_webhook(table, shop: str, store_number: str, token: str, raw_body: str, api_version: str) -> None:
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return
    oid = body.get("admin_graphql_api_id")
    if not oid and body.get("id") is not None:
        oid = f"gid://shopify/Order/{body['id']}"
    if not oid:
        return
    data = graphql_request(shop, token, ORDER_ONE_Q, {"id": oid}, api_version=api_version)
    node = data.get("data", {}).get("order")
    if not node:
        return
    meta = _shop_row(table, shop) or {}
    protection_gid = meta.get("protection_product_gid")
    has_prot = order_has_protection_product(node, protection_gid)
    pk_t = pk_tenant(store_number)
    gid = node["id"]
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": pk_t,
            "sk": f"ORDER#{gid}",
            "payload": json.dumps(node, default=str),
            "updated_at_source": node.get("updatedAt"),
            "synced_at": now,
            "shopify_id": gid,
            "has_shipping_protection": has_prot,
            "sync_tags": order_sync_tags(has_prot, protection_gid),
        }
    )

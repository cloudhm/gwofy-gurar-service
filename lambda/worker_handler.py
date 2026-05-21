"""SQS worker — internal jobs + Shopify webhooks (async only)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3

from lib.activate_app import ActivateAppError, run_activate_app_safe
from lib.admin_shop_sync import run_admin_shop_sync
from lib.customer_order_sync import fetch_merged_order_node, sync_orders
from lib.feishu import send_text
from lib.logging_json import setup_logging
from lib.models import SK_METADATA, SK_WEBHOOK_PROCESSED, pk_shop, pk_tenant, pk_webhook
from lib.shop_offline_access import get_fresh_shop_access_token
from lib.shop_archive import archive_and_delete_shop
from lib.activity_config import ensure_activity_info_seed
from lib.calc_coverage_tips_config import ensure_calc_coverage_tips_seed
from lib.tips_config import ensure_tips_info_seed
from lib.pricing_config import ensure_default_pricing_seed
from lib.max_coverage_config import ensure_max_coverage_seed
from lib.shipping_country_defaults import ensure_shipping_country_defaults_seed
from lib.markets_sync import sync_market_rates_after_profile
from lib.product_sync import _snapshot_hash as product_snapshot_hash
from lib.product_sync import fetch_merged_product_node
from lib.product_sync import product_snapshot_from_graphql
from lib.product_sync import sync_products_initial
from lib.shop_profile_sync import sync_shop_profile
from lib.theme_sync import sync_themes_full
from lib.shopify_api import DEFAULT_API_VERSION, graphql_request
from lib.sync_denorm import denorm_order_top_fields, denorm_product_top_fields
from lib.order_protection import order_has_protection_product
from lib.sync_order_tags import order_sync_tags

logger = setup_logging("worker")

ddb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")
kms_key_id_env = None


def _shop_admin_token(table, shop: str, kms_key_id: str) -> str:
    return get_fresh_shop_access_token(
        table,
        shop,
        kms_key_id_fallback=kms_key_id,
        client_id=os.environ["SHOPIFY_CLIENT_ID"],
        client_secret=os.environ["SHOPIFY_CLIENT_SECRET"],
    )


def _sqs_record_audit(record: dict[str, Any]) -> dict[str, Any]:
    attrs = record.get("attributes") or {}
    return {
        "sqs_message_id": record.get("messageId") or "",
        "sqs_receive_count": attrs.get("ApproximateReceiveCount") or "",
        "sqs_sent_timestamp": attrs.get("SentTimestamp") or "",
        "sqs_first_receive_timestamp": attrs.get("ApproximateFirstReceiveTimestamp") or "",
    }


def _job_summary(body: dict[str, Any]) -> dict[str, Any]:
    """Safe one-line context for CloudWatch (no tokens, no webhook bodies)."""
    src = body.get("source")
    summary: dict[str, Any] = {
        "job_source": src,
        "job_event": body.get("event"),
        "shop": body.get("shop"),
        "store_number": body.get("store_number"),
    }
    if src == "webhook_ingress":
        headers = {k.lower(): v for k, v in (body.get("headers") or {}).items()}
        summary["webhook_topic"] = headers.get("x-shopify-topic") or ""
        wid = (headers.get("x-shopify-webhook-id") or "").strip()
        summary["webhook_id"] = wid[:48] if wid else ""
        summary["webhook_body_len"] = len(body.get("body") or "")
    if src == "reconcile":
        summary["reconcile_resource"] = body.get("resource")
    if src == "admin_api":
        summary["admin_sync_resources"] = body.get("resources")
    return summary


def _oauth_install_job_sort_key(body: dict[str, Any]) -> int:
    """Within one Lambda invocation, run lightweight install jobs before INITIAL_SYNC."""
    if body.get("source") != "oauth":
        return 100
    ev = body.get("event")
    if ev == "APP_INSTALLED":
        return 0
    if ev == "INITIAL_SYNC":
        return 1
    if ev in ("CATALOG_SYNC", "THEME_SYNC"):
        return 2
    return 50


def _sort_worker_sqs_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable order: APP_INSTALLED → other oauth → INITIAL_SYNC; tie-break by receive order."""
    keyed: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for i, rec in enumerate(records):
        try:
            body = json.loads(rec.get("body") or "")
        except json.JSONDecodeError:
            keyed.append(((999, i), rec))
            continue
        keyed.append(((_oauth_install_job_sort_key(body), i), rec))
    keyed.sort(key=lambda t: t[0])
    return [rec for _, rec in keyed]


def handler(event, context):
    table_name = os.environ["TABLE_NAME"]
    kms_key_id = os.environ["KMS_KEY_ID"]
    api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
    feishu_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    table = ddb.Table(table_name)

    raw_records = event.get("Records", [])
    records = _sort_worker_sqs_records(raw_records)
    req_id = getattr(context, "aws_request_id", "") or ""
    logger.info(
        "worker_sqs_batch_start",
        extra={
            "record_count": len(records),
            "lambda_request_id": req_id,
            "sqs_reordered": records != raw_records,
        },
    )

    batch_item_failures: list[dict[str, str]] = []

    for record in records:
        sqs_ctx = _sqs_record_audit(record)
        t0 = time.perf_counter()
        msg_id = record.get("messageId") or ""
        try:
            body = json.loads(record["body"])
        except json.JSONDecodeError:
            logger.exception("worker_sqs_body_invalid_json", extra=sqs_ctx)
            if msg_id:
                batch_item_failures.append({"itemIdentifier": msg_id})
            continue
        job = _job_summary(body)
        logger.info("worker_sqs_record_start", extra={**sqs_ctx, **job})
        try:
            route_message(table, body, kms_key_id, api_version, feishu_url)
        except Exception:
            logger.exception(
                "worker_record_failed",
                extra={**sqs_ctx, **job, "body_preview": json.dumps(body, default=str)[:300]},
            )
            if msg_id:
                batch_item_failures.append({"itemIdentifier": msg_id})
            continue
        duration_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("worker_sqs_record_done", extra={**sqs_ctx, **job, "duration_ms": duration_ms})

    return {"batchItemFailures": batch_item_failures}


def route_message(table, body: dict[str, Any], kms_key_id: str, api_version: str, feishu_url: str) -> None:
    src = body.get("source")
    if src == "oauth":
        ev = body.get("event")
        shop = body["shop"]
        store_number = body["store_number"]
        if ev == "INITIAL_SYNC":
            run_install_bootstrap(table, shop, store_number, kms_key_id, api_version)
        elif ev == "CATALOG_SYNC":
            run_catalog_sync(table, shop, store_number, kms_key_id, api_version)
        elif ev == "THEME_SYNC":
            run_theme_sync(table, shop, store_number, kms_key_id, api_version)
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
        token = _shop_admin_token(table, shop, kms_key_id)
        ver = body.get("api_version") or api_version
        if ev == "SHOP_PROFILE_SYNC":
            sync_shop_profile(table, shop, token, ver)
            return
        logger.warning("unknown_merchant_event", extra={"event": ev})
        return

    if src == "reconcile":
        shop = body["shop"]
        store_number = body["store_number"]
        run_install_bootstrap(
            table, shop, store_number, kms_key_id, api_version, enqueue_catalog=False
        )
        run_catalog_sync(table, shop, store_number, kms_key_id, api_version)
        _advance_reconcile_marker(table, shop, body.get("resource", "ALL"))
        return

    if src == "admin_api":
        shop = body["shop"]
        store_number = str(body.get("store_number", ""))
        meta = _shop_row(table, shop)
        if not meta or meta.get("installation_status") != "ACTIVE":
            logger.info("skip_admin_sync_inactive", extra={"shop": shop})
            return
        enc = meta.get("access_token_enc")
        if not enc:
            logger.warning("skip_admin_sync_no_token", extra={"shop": shop})
            return
        token = _shop_admin_token(table, shop, kms_key_id)
        ver = body.get("api_version") or api_version
        resources = body.get("resources") or ["all"]
        reset_cp = bool(body.get("reset_checkpoints"))
        run_admin_shop_sync(
            table,
            shop,
            store_number,
            token,
            kms_key_id,
            ver,
            resources if isinstance(resources, list) else [str(resources)],
            reset_checkpoints=reset_cp,
        )
        return

    if src == "webhook_ingress":
        process_webhook_envelope(table, body, kms_key_id, api_version, feishu_url)
        return

    logger.warning("unknown_message_source", extra={"source": src})


def _shop_row(table, shop: str) -> dict[str, Any] | None:
    return table.get_item(Key={"pk": pk_shop(shop), "sk": SK_METADATA}).get("Item")


def _product_gid_from_webhook_body(body: dict[str, Any]) -> str | None:
    pid = body.get("admin_graphql_api_id")
    if not pid and body.get("id") is not None:
        pid = f"gid://shopify/Product/{body['id']}"
    return str(pid).strip() if pid else None


def _order_gid_from_webhook_body(body: dict[str, Any]) -> str | None:
    oid = body.get("admin_graphql_api_id")
    if not oid and body.get("id") is not None:
        oid = f"gid://shopify/Order/{body['id']}"
    return str(oid).strip() if oid else None


def handle_product_deleted(table, shop: str, store_number: str, raw_body: str) -> None:
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return
    gid = _product_gid_from_webhook_body(body)
    if not gid:
        logger.warning("product_delete_missing_id", extra={"shop": shop})
        return
    pk_t = pk_tenant(store_number)
    sk_p = f"PRODUCT#{gid}"
    prev = table.get_item(Key={"pk": pk_t, "sk": sk_p}).get("Item")
    now = datetime.now(timezone.utc).isoformat()
    prev_payload: dict[str, Any] = {}
    if prev:
        try:
            prev_payload = json.loads(prev.get("payload") or "{}")
        except json.JSONDecodeError:
            prev_payload = {}
    tomb_snap = {
        "gid": gid,
        "deleted": True,
        "variants": [],
        "handle": prev_payload.get("handle"),
        "title": prev_payload.get("title"),
        "status": "DELETED",
    }
    h = product_snapshot_hash(tomb_snap)
    snap_for_denorm = {
        "gid": gid,
        "handle": prev_payload.get("handle") or "",
        "title": prev_payload.get("title") or "",
        "status": "DELETED",
        "variants": [],
    }
    denorm = denorm_product_top_fields(snap_for_denorm)
    denorm["sync_deleted"] = True
    denorm["deleted_at"] = now
    payload_out = (prev or {}).get("payload") or json.dumps({"gid": gid, "sync_deleted": True}, default=str)
    table.put_item(
        Item={
            "pk": pk_t,
            "sk": sk_p,
            "payload": payload_out,
            "snapshot_hash": h,
            "updated_at_source": now,
            "synced_at": now,
            "shopify_id": gid,
            **denorm,
        }
    )
    logger.info("product_mirror_deleted", extra={"shop": shop, "gid": gid})


def handle_order_deleted(table, shop: str, store_number: str, raw_body: str) -> None:
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        return
    gid = _order_gid_from_webhook_body(body)
    if not gid:
        logger.warning("order_delete_missing_id", extra={"shop": shop})
        return
    pk_t = pk_tenant(store_number)
    sk_p = f"ORDER#{gid}"
    prev = table.get_item(Key={"pk": pk_t, "sk": sk_p}).get("Item")
    now = datetime.now(timezone.utc).isoformat()
    prev_payload: dict[str, Any] = {}
    if prev:
        try:
            prev_payload = json.loads(prev.get("payload") or "{}")
        except json.JSONDecodeError:
            prev_payload = {}
    tomb_snap = {"gid": gid, "deleted": True}
    h = product_snapshot_hash(tomb_snap)
    denorm = denorm_order_top_fields(
        {
            "name": prev_payload.get("name"),
            "legacyResourceId": prev_payload.get("legacyResourceId"),
        }
    )
    denorm["sync_deleted"] = True
    denorm["deleted_at"] = now
    payload_out = (prev or {}).get("payload") or json.dumps({"id": gid, "sync_deleted": True}, default=str)
    table.put_item(
        Item={
            "pk": pk_t,
            "sk": sk_p,
            "payload": payload_out,
            "snapshot_hash": h,
            "updated_at_source": now,
            "synced_at": now,
            "shopify_id": gid,
            "has_shipping_protection": False,
            "sync_tags": [],
            **denorm,
        }
    )
    logger.info("order_mirror_deleted", extra={"shop": shop, "gid": gid})


def _maybe_auto_activate(
    table,
    shop: str,
    store_number: str,
    token: str,
    kms_key_id: str,
    api_version: str,
    meta: dict[str, Any],
) -> None:
    """After install profile sync, create/update Shipping Protection product if not yet activated."""
    if str(meta.get("activation_status") or "") == "ACTIVATED":
        logger.info("auto_activate_skip_already_activated", extra={"shop": shop})
        return
    logger.info("initial_sync_phase", extra={"phase": "auto_activate", "shop": shop})
    try:
        run_activate_app_safe(
            table,
            shop,
            store_number,
            token,
            kms_key_id,
            api_version,
            actor_sub="initial_sync",
        )
        logger.info("auto_activate_success", extra={"shop": shop, "store_number": store_number})
    except ActivateAppError as e:
        logger.warning(
            "auto_activate_business_error",
            extra={"shop": shop, "code": e.code, "detail": e.message[:500]},
        )
    except Exception:
        logger.exception("auto_activate_failed", extra={"shop": shop})


def _enqueue_catalog_sync(shop: str, store_number: str, api_version: str) -> None:
    q = os.environ.get("WORK_QUEUE_URL")
    if not q:
        logger.warning("catalog_sync_enqueue_skipped_no_queue", extra={"shop": shop})
        return
    body = {
        "source": "oauth",
        "event": "CATALOG_SYNC",
        "shop": shop,
        "store_number": store_number,
        "api_version": api_version,
    }
    r = sqs.send_message(QueueUrl=q, MessageBody=json.dumps(body))
    logger.info(
        "catalog_sync_enqueued",
        extra={"shop": shop, "store_number": store_number, "sqs_message_id": r.get("MessageId")},
    )


def _enqueue_theme_sync(shop: str, store_number: str, api_version: str) -> None:
    q = os.environ.get("WORK_QUEUE_URL")
    if not q:
        logger.warning("theme_sync_enqueue_skipped_no_queue", extra={"shop": shop})
        return
    body = {
        "source": "oauth",
        "event": "THEME_SYNC",
        "shop": shop,
        "store_number": store_number,
        "api_version": api_version,
    }
    r = sqs.send_message(QueueUrl=q, MessageBody=json.dumps(body))
    logger.info(
        "theme_sync_enqueued",
        extra={"shop": shop, "store_number": store_number, "sqs_message_id": r.get("MessageId")},
    )


def _ensure_global_config_seeds(table_name: str) -> None:
    ensure_default_pricing_seed(table_name)
    ensure_shipping_country_defaults_seed(table_name)
    ensure_max_coverage_seed(table_name)
    ensure_activity_info_seed(table_name)
    ensure_tips_info_seed(table_name)
    ensure_calc_coverage_tips_seed(table_name)


def run_install_bootstrap(
    table,
    shop: str,
    store_number: str,
    kms_key_id: str,
    api_version: str,
    *,
    enqueue_catalog: bool = True,
    enqueue_themes: bool = True,
) -> None:
    """Shop profile + markets/currency, then activate; optionally enqueue async catalog/theme sync."""
    logger.info(
        "install_bootstrap_start",
        extra={"shop": shop, "store_number": store_number, "api_version": api_version},
    )
    meta = _shop_row(table, shop)
    if not meta or meta.get("installation_status") != "ACTIVE":
        logger.info("skip_sync_not_active", extra={"shop": shop})
        return
    enc = meta.get("access_token_enc")
    if not enc:
        logger.warning("skip_sync_no_token", extra={"shop": shop})
        return
    token = _shop_admin_token(table, shop, kms_key_id)
    table_name = os.environ["TABLE_NAME"]
    _ensure_global_config_seeds(table_name)
    logger.info("install_bootstrap_phase", extra={"phase": "shop_profile", "shop": shop})
    sync_shop_profile(table, shop, token, api_version)
    meta = _shop_row(table, shop) or {}
    _maybe_auto_activate(table, shop, store_number, token, kms_key_id, api_version, meta)
    if enqueue_catalog:
        _enqueue_catalog_sync(shop, store_number, api_version)
    if enqueue_themes:
        _enqueue_theme_sync(shop, store_number, api_version)
    logger.info("install_bootstrap_complete", extra={"shop": shop, "store_number": store_number})


def run_catalog_sync(table, shop: str, store_number: str, kms_key_id: str, api_version: str) -> None:
    """Async full pull: products then orders (may run long after install activation)."""
    logger.info(
        "catalog_sync_start",
        extra={"shop": shop, "store_number": store_number, "api_version": api_version},
    )
    meta = _shop_row(table, shop)
    if not meta or meta.get("installation_status") != "ACTIVE":
        logger.info("skip_catalog_sync_not_active", extra={"shop": shop})
        return
    enc = meta.get("access_token_enc")
    if not enc:
        logger.warning("skip_catalog_sync_no_token", extra={"shop": shop})
        return
    token = _shop_admin_token(table, shop, kms_key_id)
    protection_gid = meta.get("protection_product_gid")
    logger.info("catalog_sync_phase", extra={"phase": "products", "shop": shop})
    sync_products_initial(table, shop, store_number, token, api_version)
    meta = _shop_row(table, shop) or {}
    protection_gid = meta.get("protection_product_gid") or protection_gid
    logger.info("catalog_sync_phase", extra={"phase": "orders", "shop": shop})
    sync_orders(
        table, shop, store_number, token, api_version, protection_product_gid=protection_gid
    )
    logger.info("catalog_sync_complete", extra={"shop": shop, "store_number": store_number})


def run_theme_sync(table, shop: str, store_number: str, kms_key_id: str, api_version: str) -> None:
    """Async pull: online store themes and theme files."""
    logger.info(
        "theme_sync_start",
        extra={"shop": shop, "store_number": store_number, "api_version": api_version},
    )
    meta = _shop_row(table, shop)
    if not meta or meta.get("installation_status") != "ACTIVE":
        logger.info("skip_theme_sync_not_active", extra={"shop": shop})
        return
    enc = meta.get("access_token_enc")
    if not enc:
        logger.warning("skip_theme_sync_no_token", extra={"shop": shop})
        return
    token = _shop_admin_token(table, shop, kms_key_id)
    try:
        sync_themes_full(table, shop, token, api_version)
    except Exception:
        logger.warning("theme_sync_failed", extra={"shop": shop}, exc_info=True)
        return
    logger.info("theme_sync_complete", extra={"shop": shop, "store_number": store_number})


def run_initial_sync(table, shop: str, store_number: str, kms_key_id: str, api_version: str) -> None:
    """Legacy entry: install bootstrap only (catalog sync is enqueued separately)."""
    run_install_bootstrap(table, shop, store_number, kms_key_id, api_version)


def _advance_reconcile_marker(table, shop: str, resource: str) -> None:
    shop_norm = shop.strip().lower().rstrip("/")
    pk = f"SYNC#{shop_norm}"
    sk = f"RECONCILE#{resource}"
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(Item={"pk": pk, "sk": sk, "last_reconciled_at": now})


def _webhook_delivery_already_processed(table, webhook_id: str) -> bool:
    if not webhook_id:
        return False
    key = {"pk": pk_webhook(webhook_id), "sk": SK_WEBHOOK_PROCESSED}
    return bool(table.get_item(Key=key).get("Item"))


def _mark_webhook_delivery_processed(
    table, webhook_id: str, topic: str, shop: str
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "pk": pk_webhook(webhook_id),
            "sk": SK_WEBHOOK_PROCESSED,
            "processed_at": now,
            "topic": topic,
            "shop": shop,
        }
    )


def process_webhook_envelope(
    table,
    envelope: dict[str, Any],
    kms_key_id: str,
    api_version: str,
    feishu_url: str,
) -> None:
    headers = {k.lower(): v for k, v in (envelope.get("headers") or {}).items()}
    topic = headers.get("x-shopify-topic") or ""
    webhook_id = (headers.get("x-shopify-webhook-id") or "").strip()
    shop = (headers.get("x-shopify-shop-domain") or "").strip()

    if webhook_id and _webhook_delivery_already_processed(table, webhook_id):
        logger.info(
            "webhook_duplicate_skip",
            extra={"webhook_id": webhook_id, "topic": topic, "shop": shop},
        )
        return

    _execute_webhook_envelope(table, envelope, headers, kms_key_id, api_version, feishu_url)

    if webhook_id:
        _mark_webhook_delivery_processed(table, webhook_id, topic, shop)


def _execute_webhook_envelope(
    table,
    envelope: dict[str, Any],
    headers: dict[str, Any],
    kms_key_id: str,
    api_version: str,
    feishu_url: str,
) -> None:
    topic = headers.get("x-shopify-topic") or ""
    webhook_id = (headers.get("x-shopify-webhook-id") or "").strip()
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
    token = _shop_admin_token(table, shop, kms_key_id)

    if t in ("shop/update", "markets/create", "markets/update"):
        try:
            sync_shop_profile(table, shop, token, api_version)
        except Exception:
            logger.exception("shop_profile_webhook_failed", extra={"shop": shop, "topic": t})
        return

    if t == "markets/delete":
        try:
            sync_market_rates_after_profile(
                table,
                shop,
                token,
                api_version,
                billing_country=str(meta.get("billing_country_code") or ""),
            )
        except Exception:
            logger.exception("markets_delete_resync_failed", extra={"shop": shop})
        return

    if t == "products/delete":
        handle_product_deleted(table, shop, store_number, envelope.get("body") or "")
        return

    if t == "orders/delete":
        handle_order_deleted(table, shop, store_number, envelope.get("body") or "")
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
    try:
        node = fetch_merged_product_node(shop, token, pid, api_version)
    except Exception as exc:
        logger.warning("product_fetch_errors", extra={"error": str(exc)[:500], "shop": shop})
        return
    if not node:
        fake_body = json.dumps({"admin_graphql_api_id": pid})
        handle_product_deleted(table, shop, store_number, fake_body)
        return
    from lib import product_sync as ps

    gid = node["id"]
    snap = product_snapshot_from_graphql(node)
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
            "sync_deleted": False,
            "deleted_at": None,
            **denorm_product_top_fields(snap),
        }
    )


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
    try:
        node = fetch_merged_order_node(shop, token, oid, api_version)
    except Exception as exc:
        logger.warning("order_fetch_errors", extra={"error": str(exc)[:500], "shop": shop})
        return
    if not node:
        handle_order_deleted(table, shop, store_number, json.dumps({"admin_graphql_api_id": oid}))
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
            "sync_deleted": False,
            "deleted_at": None,
            **denorm_order_top_fields(node),
        }
    )

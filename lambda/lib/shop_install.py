"""Shop install: persist offline tokens to METADATA and enqueue worker bootstrap (OAuth or session)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

from .kms_tokens import encrypt_refresh_token, encrypt_token
from .models import GSI2_PK_SHOP_INDEX, SK_METADATA, pk_shop
from .store_number import allocate_store_number

_PRESERVE_FROM_PREV = (
    "activation_status",
    "protection_product_gid",
    "protection_product_handle",
    "embed_enabled_ack",
    "return_insurance_status",
    "shipping_protection_status",
    "plugin_suspended",
    "sp_below_min_coverage_tip",
    "sp_greater_max_coverage_tip",
)


def shop_needs_install_bootstrap(item: dict[str, Any] | None) -> bool:
    """True when the shop has no usable offline install row (first install or re-bootstrap)."""
    if not item:
        return True
    if not item.get("access_token_enc"):
        return True
    if item.get("installation_status") != "ACTIVE":
        return True
    return False


def upsert_shop_metadata_from_offline_tokens(
    table: Any,
    table_name: str,
    shop: str,
    token_resp: dict[str, Any],
    kms_key_id: str,
    *,
    oauth_state_last: str = "",
) -> str:
    """
    Write shop METADATA after OAuth code exchange or session token exchange.

    Mirrors ``oauth_handler`` install row shape. Returns ``store_number``.
    """
    shop_norm = shop.strip().lower().rstrip("/")
    access_token = str(token_resp.get("access_token") or "")
    if not access_token:
        raise ValueError("token_response_missing_access_token")
    scopes = token_resp.get("scope") or ""

    store_number = allocate_store_number(table_name, shop_norm)
    enc = encrypt_token(kms_key_id, access_token)

    pk = pk_shop(shop_norm)
    now = datetime.now(timezone.utc).isoformat()
    now_dt = datetime.now(timezone.utc)
    prev = table.get_item(Key={"pk": pk, "sk": SK_METADATA}).get("Item") or {}
    installed_at = str(prev.get("installed_at") or now)

    item: dict[str, Any] = {
        "pk": pk,
        "sk": SK_METADATA,
        "shop": shop_norm,
        "store_number": store_number,
        "access_token_enc": enc,
        "scopes": scopes,
        "installation_status": "ACTIVE",
        "installed_at": installed_at,
        "updated_at": now,
        "kms_key_id": kms_key_id,
        "oauth_state_last": oauth_state_last,
        "activation_status": "UNACTIVATED",
        "return_insurance_status": "CLOSED",
        "shipping_protection_status": "CLOSED",
        "plugin_suspended": False,
        "embed_enabled_ack": False,
        "gsi2pk": GSI2_PK_SHOP_INDEX,
        "gsi2sk": f"{installed_at}#{shop_norm}",
    }
    rt = token_resp.get("refresh_token")
    exp_in = int(token_resp.get("expires_in") or 0)
    rt_exp_in = int(token_resp.get("refresh_token_expires_in") or 0)
    if isinstance(rt, str) and rt.strip() and exp_in > 0 and rt_exp_in > 0:
        item["refresh_token_enc"] = encrypt_refresh_token(kms_key_id, rt.strip())
        item["shopify_offline_access_token_expires_at"] = (
            now_dt + timedelta(seconds=exp_in)
        ).isoformat()
        item["shopify_offline_refresh_token_expires_at"] = (
            now_dt + timedelta(seconds=rt_exp_in)
        ).isoformat()

    for k in _PRESERVE_FROM_PREV:
        if prev.get(k) is not None:
            item[k] = prev[k]

    table.put_item(Item=item)
    return store_number


def enqueue_install_worker_jobs(
    *,
    queue_url: str,
    shop: str,
    store_number: str,
    api_version: str,
    source: str = "oauth",
) -> dict[str, str | None]:
    """Enqueue APP_INSTALLED (notify) then INITIAL_SYNC (profile + auto-activate + catalog/theme)."""
    shop_norm = shop.strip().lower().rstrip("/")
    internal = {
        "source": source,
        "shop": shop_norm,
        "store_number": store_number,
        "api_version": api_version,
    }
    sqs = boto3.client("sqs")
    r_installed = sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({**internal, "event": "APP_INSTALLED"}),
    )
    r_sync = sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({**internal, "event": "INITIAL_SYNC"}),
    )
    return {
        "app_installed_message_id": r_installed.get("MessageId"),
        "initial_sync_message_id": r_sync.get("MessageId"),
    }

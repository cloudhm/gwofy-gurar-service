"""Move shop-scoped DynamoDB rows to the archive table, then delete from online."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Key

from .models import SK_METADATA, pk_shop, pk_store_number, pk_sync, pk_tenant


def query_all_items(table, pk: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("pk").eq(pk)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items") or [])
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def sanitize_item_for_archive(item: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(item)
    out.pop("access_token_enc", None)
    out.pop("refresh_token_enc", None)
    out.pop("shopify_offline_access_token_expires_at", None)
    out.pop("shopify_offline_refresh_token_expires_at", None)
    return out


def archive_and_delete_shop(online_table, archive_table, shop: str) -> int:
    """Copy shop-scoped rows to archive (no access token), then remove from online.

    Idempotent: if SHOP METADATA is already gone, returns 0.

    Does not touch ``GLOBAL#CONFIG`` or other shared keys.
    """
    pk_s = pk_shop(shop)
    meta = online_table.get_item(Key={"pk": pk_s, "sk": SK_METADATA}).get("Item")
    if not meta:
        return 0

    sn = meta.get("store_number")
    store_number = str(sn).strip() if sn else ""

    collected: list[dict[str, Any]] = []
    collected.extend(query_all_items(online_table, pk_s))

    if store_number:
        collected.extend(query_all_items(online_table, pk_tenant(store_number)))

    collected.extend(query_all_items(online_table, pk_sync(shop)))

    if store_number:
        sn_row = online_table.get_item(
            Key={"pk": pk_store_number(store_number), "sk": SK_METADATA}
        ).get("Item")
        if sn_row:
            collected.append(sn_row)

    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for it in collected:
        pk = it.get("pk")
        sk = it.get("sk")
        if not pk or not sk:
            continue
        key = (pk, sk)
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    now_iso = datetime.now(timezone.utc).isoformat()

    with archive_table.batch_writer() as batch:
        for it in unique:
            body = sanitize_item_for_archive(it)
            body["archived_at"] = now_iso
            body["archived_reason"] = "app_uninstalled"
            body["uninstalled_at"] = now_iso
            batch.put_item(Item=body)

    with online_table.batch_writer() as batch:
        for it in unique:
            batch.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})

    return len(unique)

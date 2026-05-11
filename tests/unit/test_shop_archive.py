"""Unit tests for shop unbind archive + online purge."""

from __future__ import annotations

from unittest.mock import MagicMock

from lib.models import SK_METADATA, pk_shop, pk_store_number, pk_sync, pk_tenant
from lib.shop_archive import archive_and_delete_shop, sanitize_item_for_archive


class _RecordingBatch:
    def __init__(self) -> None:
        self.puts: list[dict] = []
        self.deletes: list[dict] = []

    def put_item(self, Item: dict) -> None:
        self.puts.append(Item)

    def delete_item(self, Key: dict) -> None:
        self.deletes.append(Key)

    def __enter__(self) -> _RecordingBatch:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_sanitize_removes_access_token_enc():
    raw = {"pk": "SHOP#x", "sk": SK_METADATA, "access_token_enc": b"secret", "shop": "x"}
    out = sanitize_item_for_archive(raw)
    assert "access_token_enc" not in out
    assert out["shop"] == "x"
    assert "access_token_enc" in raw


def test_archive_returns_zero_when_no_metadata():
    online = MagicMock()
    online.get_item.return_value = {}
    archive = MagicMock()
    assert archive_and_delete_shop(online, archive, "missing.myshopify.com") == 0
    online.query.assert_not_called()
    archive.batch_writer.assert_not_called()


def test_archive_queries_partitions_and_strips_token():
    shop = "acme.myshopify.com"
    pk_s = pk_shop(shop)
    pk_t = pk_tenant("1234567890")
    pk_sy = pk_sync(shop)
    pk_sn = pk_store_number("1234567890")

    meta = {"pk": pk_s, "sk": SK_METADATA, "store_number": "1234567890", "access_token_enc": "enc"}
    shop_audit = {"pk": pk_s, "sk": "AUDIT#1", "x": 1}
    tenant_row = {"pk": pk_t, "sk": "ORDER#gid", "payload": "{}"}
    sync_row = {"pk": pk_sy, "sk": "ORDERS#CHECKPOINT", "graphql_page_cursor": None}
    store_row = {"pk": pk_sn, "sk": SK_METADATA, "shop": shop}

    online = MagicMock()
    archive = MagicMock()

    online.get_item.side_effect = [
        {"Item": meta},
        {"Item": store_row},
    ]

    online.query.side_effect = [
        {"Items": [meta, shop_audit]},
        {"Items": [tenant_row]},
        {"Items": [sync_row]},
    ]

    arc_batch = _RecordingBatch()
    onl_batch = _RecordingBatch()
    archive.batch_writer.return_value = arc_batch
    online.batch_writer.return_value = onl_batch

    n = archive_and_delete_shop(online, archive, shop)
    assert n == 5

    meta_put = next(p for p in arc_batch.puts if p["sk"] == SK_METADATA and p["pk"] == pk_s)
    assert "access_token_enc" not in meta_put
    assert meta_put["archived_reason"] == "app_uninstalled"
    assert "archived_at" in meta_put
    assert "uninstalled_at" in meta_put

    deleted_keys = {(d["pk"], d["sk"]) for d in onl_batch.deletes}
    assert (pk_s, SK_METADATA) in deleted_keys
    assert (pk_s, "AUDIT#1") in deleted_keys
    assert (pk_t, "ORDER#gid") in deleted_keys
    assert (pk_sy, "ORDERS#CHECKPOINT") in deleted_keys
    assert (pk_sn, SK_METADATA) in deleted_keys

    assert online.query.call_count == 3

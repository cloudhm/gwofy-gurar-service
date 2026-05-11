"""Product GraphQL sync + price/meta history diff (§1f)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .models import pk_tenant
from .shopify_api import graphql_request


PRODUCTS_QUERY = """
query ProductsPage($cursor: String) {
  products(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
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
  }
}
"""


def _flatten_metafields(edges: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in edges or []:
        n = e.get("node") or {}
        k = f"{n.get('namespace','')}:{n.get('key','')}"
        out[k] = str(n.get("value", ""))
    return out


def _snapshot_hash(product: dict[str, Any]) -> str:
    raw = json.dumps(product, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def sync_products_initial(
    table,
    shop: str,
    store_number: str,
    token: str,
    api_version: str,
) -> None:
    """Full product pagination with checkpoint on SYNC#shop PRODUCTS#CHECKPOINT."""
    cursor = None
    shop_norm = shop.strip().lower().rstrip("/")
    sync_pk = f"SYNC#{shop_norm}"

    cp = table.get_item(Key={"pk": sync_pk, "sk": "PRODUCTS#CHECKPOINT"}).get("Item")
    if cp and cp.get("graphql_page_cursor"):
        cursor = cp.get("graphql_page_cursor")

    while True:
        data = graphql_request(
            shop,
            token,
            PRODUCTS_QUERY,
            {"cursor": cursor},
            api_version=api_version,
        )
        errors = data.get("errors")
        if errors:
            raise RuntimeError(f"GraphQL errors: {errors}")
        conn = data["data"]["products"]
        for edge in conn["edges"]:
            node = edge["node"]
            gid = node["id"]
            snap = {
                "gid": gid,
                "updatedAt": node.get("updatedAt"),
                "variants": [],
                "product_metafields": _flatten_metafields(
                    (node.get("metafields") or {}).get("edges") or []
                ),
            }
            for ve in (node.get("variants") or {}).get("edges") or []:
                vn = ve["node"]
                snap["variants"].append(
                    {
                        "id": vn["id"],
                        "price": vn.get("price"),
                        "compareAtPrice": vn.get("compareAtPrice"),
                        "updatedAt": vn.get("updatedAt"),
                        "metafields": _flatten_metafields(
                            (vn.get("metafields") or {}).get("edges") or []
                        ),
                    }
                )
            h = _snapshot_hash(snap)
            pk_t = pk_tenant(store_number)
            sk_p = f"PRODUCT#{gid}"
            prev = table.get_item(Key={"pk": pk_t, "sk": sk_p}).get("Item")
            prev_hash = (prev or {}).get("snapshot_hash")

            now = datetime.now(timezone.utc).isoformat()
            if prev_hash != h:
                _write_history_if_changed(table, pk_t, store_number, prev, snap, now)

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

        page = conn["pageInfo"]
        cursor = page.get("endCursor")
        has_next = page.get("hasNextPage")

        if cursor:
            table.put_item(
                Item={
                    "pk": sync_pk,
                    "sk": "PRODUCTS#CHECKPOINT",
                    "graphql_page_cursor": cursor if has_next else None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        if not has_next:
            table.put_item(
                Item={
                    "pk": sync_pk,
                    "sk": "PRODUCTS#CHECKPOINT",
                    "graphql_page_cursor": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            break


def _write_history_if_changed(
    table,
    pk_t: str,
    store_number: str,
    prev_item: dict[str, Any] | None,
    snap: dict[str, Any],
    observed_at: str,
) -> None:
    if not prev_item:
        return
    try:
        prev_snap = json.loads(prev_item.get("payload") or "{}")
    except json.JSONDecodeError:
        prev_snap = {}

    gid = snap["gid"]
    rev_ts = observed_at.replace(":", "").replace("-", "")[:15]

    old_vars = {v["id"]: v for v in prev_snap.get("variants") or []}
    for v in snap["variants"]:
        vid = v["id"]
        ov = old_vars.get(vid) or {}
        if ov.get("price") != v.get("price") or ov.get("compareAtPrice") != v.get("compareAtPrice"):
            sk_h = f"VARIANT#{vid}#PRICE_VER#{rev_ts}"
            gsi1pk = f"TENANT#{store_number}#VARIANT#{vid}"
            table.put_item(
                Item={
                    "pk": pk_t,
                    "sk": sk_h,
                    "amount": str(v.get("price")),
                    "currency_hint": "shop_money",
                    "valid_from": observed_at,
                    "gsi1pk": gsi1pk,
                    "gsi1sk": observed_at,
                    "variant_id": vid,
                    "product_id": gid,
                }
            )

    prev_pm = prev_snap.get("product_metafields") or {}
    cur_pm = snap.get("product_metafields") or {}
    if prev_pm != cur_pm:
        sk_m = f"PRODUCT#{gid}#META_VER#{rev_ts}"
        table.put_item(
            Item={
                "pk": pk_t,
                "sk": sk_m,
                "owner_scope": "product",
                "values_json": json.dumps(cur_pm, ensure_ascii=False),
                "observed_at": observed_at,
                "gsi1pk": f"TENANT#{store_number}#PRODUCT_META#{gid}",
                "gsi1sk": observed_at,
            }
        )

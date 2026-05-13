"""Orders GraphQL pagination into TENANT#."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .models import pk_tenant
from .order_protection import order_has_protection_product
from .shopify_api import graphql_request
from .sync_order_tags import order_sync_tags

ORDERS_Q = """
query OrdersPage($cursor: String) {
  orders(first: 50, after: $cursor, sortKey: UPDATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
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
  }
}
"""


def sync_orders(
    table,
    shop: str,
    store_number: str,
    token: str,
    api_version: str,
    protection_product_gid: str | None = None,
) -> None:
    shop_norm = shop.strip().lower().rstrip("/")
    sync_pk = f"SYNC#{shop_norm}"
    cursor = None
    cp = table.get_item(Key={"pk": sync_pk, "sk": "ORDERS#CHECKPOINT"}).get("Item")
    if cp and cp.get("graphql_page_cursor"):
        cursor = cp["graphql_page_cursor"]

    pk_t = pk_tenant(store_number)
    while True:
        now = datetime.now(timezone.utc).isoformat()
        data = graphql_request(shop_norm, token, ORDERS_Q, {"cursor": cursor}, api_version=api_version)
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        conn = data["data"]["orders"]
        for edge in conn["edges"]:
            n = edge["node"]
            gid = n["id"]
            has_prot = order_has_protection_product(n, protection_product_gid)
            table.put_item(
                Item={
                    "pk": pk_t,
                    "sk": f"ORDER#{gid}",
                    "payload": json.dumps(n, default=str),
                    "updated_at_source": n.get("updatedAt"),
                    "synced_at": now,
                    "shopify_id": gid,
                    "has_shipping_protection": has_prot,
                    "sync_tags": order_sync_tags(has_prot, protection_product_gid),
                }
            )
        page = conn["pageInfo"]
        cursor = page.get("endCursor")
        has_next = page.get("hasNextPage")
        table.put_item(
            Item={
                "pk": sync_pk,
                "sk": "ORDERS#CHECKPOINT",
                "graphql_page_cursor": cursor if has_next else None,
                "updated_at": now,
            }
        )
        if not has_next:
            break

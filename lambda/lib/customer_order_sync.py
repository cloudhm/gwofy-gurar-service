"""Orders GraphQL pagination into TENANT#."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .models import pk_tenant
from .order_protection import order_has_protection_product
from .shop_offline_access import ShopAdminAuth, shop_admin_graphql_call
from .sync_denorm import denorm_order_top_fields
from .sync_order_tags import order_sync_tags

_ORDER_HEADER_FIELDS = """
        id
        legacyResourceId
        updatedAt
        name
        processedAt
        createdAt
        displayFinancialStatus
        displayFulfillmentStatus
        tags
        note
        currentTotalPriceSet {
          shopMoney {
            amount
            currencyCode
          }
        }
        subtotalPriceSet {
          shopMoney {
            amount
            currencyCode
          }
        }
        totalTaxSet {
          shopMoney {
            amount
            currencyCode
          }
        }
"""

_LINE_ITEM_NODE_FIELDS = """
              id
              name
              title
              sku
              quantity
              vendor
              variantTitle
              requiresShipping
              taxable
              originalUnitPriceSet {
                shopMoney {
                  amount
                  currencyCode
                }
              }
              discountedUnitPriceSet {
                shopMoney {
                  amount
                  currencyCode
                }
              }
              image {
                url
                altText
              }
              product {
                id
                title
                handle
              }
              variant {
                id
                sku
                title
                barcode
                image {
                  url
                  altText
                }
              }
"""

ORDERS_Q = (
    """
query OrdersPage($cursor: String) {
  orders(first: 50, after: $cursor, sortKey: UPDATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
"""
    + _ORDER_HEADER_FIELDS
    + """
      }
    }
  }
}
"""
)

ORDER_HEAD_Q = (
    """
query OrderHead($id: ID!) {
  order(id: $id) {
"""
    + _ORDER_HEADER_FIELDS
    + """
  }
}
"""
)

ORDER_LINES_PAGE_Q = (
    """
query OrderLines($id: ID!, $cursor: String) {
  order(id: $id) {
    lineItems(first: 250, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges {
        node {
"""
    + _LINE_ITEM_NODE_FIELDS
    + """
        }
      }
    }
  }
}
"""
)


def _raise_if_errors(data: dict[str, Any], ctx: str) -> None:
    errs = data.get("errors")
    if errs:
        raise RuntimeError(f"{ctx}: {errs}")


def merge_order_all_line_items(
    shop: str,
    token: str,
    order_head: dict[str, Any],
    api_version: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> dict[str, Any]:
    """Attach all line items (paginated) to an order node that has header fields only."""
    shop_norm = shop.strip().lower().rstrip("/")
    oid = order_head["id"]
    out = {**order_head}
    edges: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = shop_admin_graphql_call(
            shop_norm,
            token,
            ORDER_LINES_PAGE_Q,
            {"id": oid, "cursor": cursor},
            api_version,
            auth=auth,
            operation="orderLineItems",
        )
        _raise_if_errors(data, "order_line_items")
        conn = ((data.get("data") or {}).get("order") or {}).get("lineItems") or {}
        edges.extend(conn.get("edges") or [])
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
    out["lineItems"] = {"edges": edges}
    return out


def fetch_merged_order_node(
    shop: str,
    token: str,
    order_gid: str,
    api_version: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> dict[str, Any] | None:
    """Load order header + every line item page (e.g. webhook refresh)."""
    shop_norm = shop.strip().lower().rstrip("/")
    data = shop_admin_graphql_call(
        shop_norm,
        token,
        ORDER_HEAD_Q,
        {"id": order_gid},
        api_version,
        auth=auth,
        operation="orderHead",
    )
    _raise_if_errors(data, "order_head")
    head = (data.get("data") or {}).get("order")
    if not head:
        return None
    return merge_order_all_line_items(shop_norm, token, head, api_version, auth=auth)


def sync_orders(
    table,
    shop: str,
    store_number: str,
    token: str,
    api_version: str,
    protection_product_gid: str | None = None,
    *,
    auth: ShopAdminAuth | None = None,
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
        data = shop_admin_graphql_call(
            shop_norm,
            token,
            ORDERS_Q,
            {"cursor": cursor},
            api_version,
            auth=auth,
            operation="orders",
        )
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        conn = data["data"]["orders"]
        for edge in conn["edges"]:
            head = edge["node"]
            gid = head["id"]
            n = merge_order_all_line_items(shop_norm, token, head, api_version, auth=auth)
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
                    "sync_deleted": False,
                    "deleted_at": None,
                    **denorm_order_top_fields(n),
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

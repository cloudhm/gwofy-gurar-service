"""Product GraphQL sync + price/meta history diff (§1f)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .models import pk_tenant
from .shop_offline_access import ShopAdminAuth, shop_admin_graphql_call
from .sync_denorm import denorm_product_top_fields

PRODUCTS_LIST_QUERY = """
query ProductsPage($cursor: String) {
  products(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        handle
        title
        status
        updatedAt
      }
    }
  }
}
"""

PRODUCT_BASE_Q = """
query ProductBase($id: ID!) {
  product(id: $id) {
    id
    handle
    title
    status
    updatedAt
    descriptionHtml
    tags
  }
}
"""

PRODUCT_METAFIELDS_PAGE_Q = """
query ProductMetafields($id: ID!, $cursor: String) {
  product(id: $id) {
    metafields(first: 250, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges { node { namespace key type value updatedAt } }
    }
  }
}
"""

PRODUCT_MEDIA_PAGE_Q = """
query ProductMedia($id: ID!, $cursor: String) {
  product(id: $id) {
    media(first: 250, after: $cursor, sortKey: POSITION) {
      pageInfo { hasNextPage endCursor }
      edges {
        node {
          ... on MediaImage {
            id
            alt
            image {
              url
            }
          }
        }
      }
    }
  }
}
"""

_VARIANT_NODE_FIELDS = """
              id
              title
              sku
              barcode
              updatedAt
              price
              compareAtPrice
              inventoryQuantity
              selectedOptions {
                name
                value
              }
              image {
                url
                altText
              }
              metafields(first: 250) {
                pageInfo { hasNextPage endCursor }
                edges { node { namespace key type value updatedAt } }
              }
"""

PRODUCT_VARIANTS_PAGE_Q = (
    """
query ProductVariants($id: ID!, $cursor: String) {
  product(id: $id) {
    variants(first: 250, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges {
        node {
"""
    + _VARIANT_NODE_FIELDS
    + """
        }
      }
    }
  }
}
"""
)

VARIANT_METAFIELDS_PAGE_Q = """
query VariantMetafields($id: ID!, $cursor: String) {
  node(id: $id) {
    ... on ProductVariant {
      metafields(first: 250, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        edges { node { namespace key type value updatedAt } }
      }
    }
  }
}
"""


def _raise_if_errors(data: dict[str, Any], ctx: str) -> None:
    errs = data.get("errors")
    if errs:
        raise RuntimeError(f"{ctx}: {errs}")


def _paginate_product_metafield_edges(
    shop: str, token: str, product_gid: str, api_version: str, *, auth: ShopAdminAuth | None = None
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = shop_admin_graphql_call(
            shop,
            token,
            PRODUCT_METAFIELDS_PAGE_Q,
            {"id": product_gid, "cursor": cursor},
            api_version,
            auth=auth,
            operation="productGraphql",
        )
        _raise_if_errors(data, "product_metafields")
        conn = ((data.get("data") or {}).get("product") or {}).get("metafields") or {}
        edges.extend(conn.get("edges") or [])
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
    return edges


def _paginate_product_media_edges(
    shop: str, token: str, product_gid: str, api_version: str, *, auth: ShopAdminAuth | None = None
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = shop_admin_graphql_call(
            shop,
            token,
            PRODUCT_MEDIA_PAGE_Q,
            {"id": product_gid, "cursor": cursor},
            api_version,
            auth=auth,
            operation="productGraphql",
        )
        _raise_if_errors(data, "product_media")
        conn = ((data.get("data") or {}).get("product") or {}).get("media") or {}
        edges.extend(conn.get("edges") or [])
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
    return edges


def _merge_variant_metafield_pages(
    shop: str,
    token: str,
    variant_gid: str,
    mf_conn: dict[str, Any],
    api_version: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> dict[str, Any]:
    """Return metafields dict with full edges list (no pageInfo required downstream)."""
    edges: list[dict[str, Any]] = list((mf_conn or {}).get("edges") or [])
    pi = (mf_conn or {}).get("pageInfo") or {}
    cursor = pi.get("endCursor")
    while pi.get("hasNextPage"):
        data = shop_admin_graphql_call(
            shop,
            token,
            VARIANT_METAFIELDS_PAGE_Q,
            {"id": variant_gid, "cursor": cursor},
            api_version,
            auth=auth,
            operation="productGraphql",
        )
        _raise_if_errors(data, "variant_metafields")
        node = (data.get("data") or {}).get("node") or {}
        mfc = node.get("metafields") or {}
        edges.extend(mfc.get("edges") or [])
        pi = mfc.get("pageInfo") or {}
        cursor = pi.get("endCursor")
    return {"edges": edges}


def _paginate_product_variant_edges(
    shop: str, token: str, product_gid: str, api_version: str, *, auth: ShopAdminAuth | None = None
) -> list[dict[str, Any]]:
    out_edges: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = shop_admin_graphql_call(
            shop,
            token,
            PRODUCT_VARIANTS_PAGE_Q,
            {"id": product_gid, "cursor": cursor},
            api_version,
            auth=auth,
            operation="productGraphql",
        )
        _raise_if_errors(data, "product_variants")
        conn = ((data.get("data") or {}).get("product") or {}).get("variants") or {}
        for ve in conn.get("edges") or []:
            vn = ve.get("node") or {}
            if not isinstance(vn, dict) or not vn.get("id"):
                continue
            mf_root = vn.get("metafields") or {}
            vn["metafields"] = _merge_variant_metafield_pages(
                shop, token, vn["id"], mf_root, api_version, auth=auth
            )
            out_edges.append({"node": vn})
        pi = conn.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
    return out_edges


def fetch_merged_product_node(
    shop: str, token: str, product_gid: str, api_version: str, *, auth: ShopAdminAuth | None = None
) -> dict[str, Any] | None:
    """Load one product with all metafields, media, and variants (fully paginated)."""
    shop_norm = shop.strip().lower().rstrip("/")
    data = shop_admin_graphql_call(
        shop_norm,
        token,
        PRODUCT_BASE_Q,
        {"id": product_gid},
        api_version,
        auth=auth,
        operation="productBase",
    )
    _raise_if_errors(data, "product_base")
    base = (data.get("data") or {}).get("product")
    if not base:
        return None
    mf_edges = _paginate_product_metafield_edges(shop_norm, token, product_gid, api_version, auth=auth)
    media_edges = _paginate_product_media_edges(shop_norm, token, product_gid, api_version, auth=auth)
    variant_edges = _paginate_product_variant_edges(shop_norm, token, product_gid, api_version, auth=auth)
    merged: dict[str, Any] = {
        **base,
        "metafields": {"edges": mf_edges},
        "media": {"edges": media_edges},
        "variants": {"edges": variant_edges},
    }
    return merged


def _flatten_metafields(edges: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in edges or []:
        n = e.get("node") or {}
        k = f"{n.get('namespace','')}:{n.get('key','')}"
        out[k] = str(n.get("value", ""))
    return out


def _tags_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for t in raw:
        if isinstance(t, str) and t.strip():
            out.append(t)
    return out


def _media_list(node: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for me in (node.get("media") or {}).get("edges") or []:
        mn = me.get("node") or {}
        if not isinstance(mn, dict):
            continue
        img = mn.get("image") or {}
        url = img.get("url") if isinstance(img, dict) else None
        out.append(
            {
                "id": mn.get("id"),
                "url": url,
                "alt": mn.get("alt"),
            }
        )
    return out


def product_snapshot_from_graphql(node: dict[str, Any]) -> dict[str, Any]:
    """Normalize Admin API product node into the JSON we persist on PRODUCT# rows."""
    gid = node["id"]
    variants: list[dict[str, Any]] = []
    for ve in (node.get("variants") or {}).get("edges") or []:
        vn = ve.get("node") or {}
        if not isinstance(vn, dict) or not vn.get("id"):
            continue
        vimg = vn.get("image") or {}
        opts: list[dict[str, str]] = []
        for so in vn.get("selectedOptions") or []:
            if isinstance(so, dict):
                opts.append(
                    {
                        "name": str(so.get("name") or ""),
                        "value": str(so.get("value") or ""),
                    }
                )
        variants.append(
            {
                "id": vn["id"],
                "title": vn.get("title"),
                "sku": vn.get("sku"),
                "barcode": vn.get("barcode"),
                "price": vn.get("price"),
                "compareAtPrice": vn.get("compareAtPrice"),
                "updatedAt": vn.get("updatedAt"),
                "inventoryQuantity": vn.get("inventoryQuantity"),
                "selected_options": opts,
                "image_url": vimg.get("url") if isinstance(vimg, dict) else None,
                "image_alt": vimg.get("altText") if isinstance(vimg, dict) else None,
                "metafields": _flatten_metafields((vn.get("metafields") or {}).get("edges") or []),
            }
        )
    return {
        "gid": gid,
        "handle": node.get("handle"),
        "title": node.get("title"),
        "status": node.get("status"),
        "updatedAt": node.get("updatedAt"),
        "description_html": node.get("descriptionHtml"),
        "tags": _tags_list(node.get("tags")),
        "media": _media_list(node),
        "variants": variants,
        "product_metafields": _flatten_metafields((node.get("metafields") or {}).get("edges") or []),
    }


def _snapshot_hash(product: dict[str, Any]) -> str:
    raw = json.dumps(product, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def sync_products_initial(
    table,
    shop: str,
    store_number: str,
    token: str,
    api_version: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> None:
    """Full product pagination with checkpoint on SYNC#shop PRODUCTS#CHECKPOINT."""
    cursor = None
    shop_norm = shop.strip().lower().rstrip("/")
    sync_pk = f"SYNC#{shop_norm}"

    cp = table.get_item(Key={"pk": sync_pk, "sk": "PRODUCTS#CHECKPOINT"}).get("Item")
    if cp and cp.get("graphql_page_cursor"):
        cursor = cp.get("graphql_page_cursor")

    while True:
        data = shop_admin_graphql_call(
            shop_norm,
            token,
            PRODUCTS_LIST_QUERY,
            {"cursor": cursor},
            api_version,
            auth=auth,
            operation="productGraphql",
        )
        errors = data.get("errors")
        if errors:
            raise RuntimeError(f"GraphQL errors: {errors}")
        conn = data["data"]["products"]
        for edge in conn["edges"]:
            stub = edge["node"]
            gid = stub["id"]
            merged = fetch_merged_product_node(shop_norm, token, gid, api_version)
            if not merged:
                continue
            snap = product_snapshot_from_graphql(merged)
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
                    "updated_at_source": merged.get("updatedAt"),
                    "synced_at": now,
                    "shopify_id": gid,
                    "sync_deleted": False,
                    "deleted_at": None,
                    **denorm_product_top_fields(snap),
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

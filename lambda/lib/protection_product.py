"""Create or update Shipping Protection product (GraphQL Admin)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .pricing_resolve import format_money
from .shop_offline_access import ShopAdminAuth, shop_admin_graphql_call

PRODUCT_CREATE = """
mutation ProductCreate($product: ProductCreateInput!) {
  productCreate(product: $product) {
    product {
      id
      variants(first: 5) {
        nodes {
          id
          selectedOptions { name value }
        }
      }
    }
    userErrors { field message }
  }
}
"""

PRODUCT_UPDATE = """
mutation ProductUpdate($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product { id }
    userErrors { field message }
  }
}
"""

PRODUCT_VARIANTS_BULK_CREATE = """
mutation PVBC($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkCreate(productId: $productId, variants: $variants) {
    productVariants { id title }
    userErrors { field message }
  }
}
"""

PRODUCT_QUERY_VARIANTS = """
query PVQ($id: ID!, $cursor: String) {
  product(id: $id) {
    id
    variants(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        title
        selectedOptions { name value }
      }
    }
  }
}
"""

PRODUCT_VARIANTS_BULK_UPDATE = """
mutation PVBU($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id }
    userErrors { field message }
  }
}
"""

PRODUCT_VARIANTS_BULK_DELETE = """
mutation PVBD($productId: ID!, $variantsIds: [ID!]!) {
  productVariantsBulkDelete(productId: $productId, variantsIds: $variantsIds) {
    userErrors { field message }
  }
}
"""

PRODUCT_BY_ID_EXISTS = """
query ProductByIdExists($id: ID!) {
  product(id: $id) {
    id
  }
}
"""

PRODUCTS_BY_HANDLE = """
query ProductsByHandle($q: String!) {
  products(first: 5, query: $q) {
    edges {
      node {
        id
        handle
      }
    }
  }
}
"""

ONLINE_STORE_CHANNEL_NAME = "Online Store"

PUBLICATIONS_FOR_ONLINE_STORE = """
query PublicationsForOnlineStore {
  publications(first: 50) {
    nodes {
      id
      catalog { title }
      channels(first: 5) {
        nodes { name }
      }
    }
  }
}
"""

PUBLISHABLE_PUBLISH = """
mutation PublishablePublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    userErrors { field message }
  }
}
"""

def _protection_gql(
    shop: str,
    token: str,
    query: str,
    variables: dict[str, Any] | None,
    api_version: str,
    *,
    auth: ShopAdminAuth | None = None,
    operation: str = "protectionProduct",
) -> dict[str, Any]:
    return shop_admin_graphql_call(
        shop, token, query, variables or {}, api_version, auth=auth, operation=operation
    )


def _err(data: dict[str, Any], key: str) -> None:
    root = data.get("data", {}).get(key) or {}
    errs = root.get("userErrors") or []
    if errs:
        raise RuntimeError(f"shopify_user_errors:{errs}")


def _list_all_variant_nodes(shop: str, token: str, product_gid: str, api_version: str, *, auth: ShopAdminAuth | None = None) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    cursor = None
    while True:
        data = _protection_gql(
            shop, token, PRODUCT_QUERY_VARIANTS, {"id": product_gid, "cursor": cursor}, api_version, auth=auth)
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        p = data.get("data", {}).get("product") or {}
        conn = p.get("variants") or {}
        for n in conn.get("nodes") or []:
            nodes.append(n)
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return nodes


def _plan_value_from_variant(node: dict[str, Any]) -> str | None:
    for opt in node.get("selectedOptions") or []:
        if (opt.get("name") or "") == "Plan":
            return str(opt.get("value") or "")
    return None


def _product_id_exists(shop: str, token: str, api_version: str, product_gid: str, *, auth: ShopAdminAuth | None = None) -> bool:
    data = _protection_gql(
        shop, token, PRODUCT_BY_ID_EXISTS, {"id": product_gid}, api_version, auth=auth
    )
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    p = data.get("data", {}).get("product")
    return bool(p and p.get("id"))


def _first_product_gid_by_handle(
    shop: str, token: str, api_version: str, handle: str, *, auth: ShopAdminAuth | None = None
) -> str | None:
    """Admin search `handle:<value>`; returns first matching product GID."""
    h = (handle or "").strip()
    if not h:
        return None
    q = f"handle:{h}"
    data = _protection_gql(shop, token, PRODUCTS_BY_HANDLE, {"q": q}, api_version, auth=auth)
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    edges = (data.get("data", {}).get("products") or {}).get("edges") or []
    h_lower = h.lower()
    for e in edges:
        node = e.get("node") or {}
        node_handle = str(node.get("handle") or "").strip().lower()
        if node_handle != h_lower:
            continue
        gid = node.get("id")
        if gid:
            return str(gid)
    return None


def _online_store_publication_id(
    shop: str,
    token: str,
    api_version: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> str:
    """Resolve Online Store publication GID (required for in-app product search)."""
    data = _protection_gql(
        shop,
        token,
        PUBLICATIONS_FOR_ONLINE_STORE,
        {},
        api_version,
        auth=auth,
        operation="onlineStorePublication",
    )
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    nodes = (data.get("data", {}).get("publications") or {}).get("nodes") or []
    target = ONLINE_STORE_CHANNEL_NAME.casefold()
    for node in nodes:
        pub_id = node.get("id")
        if not pub_id:
            continue
        catalog = node.get("catalog") or {}
        if str(catalog.get("title") or "").casefold() == target:
            return str(pub_id)
        channels = (node.get("channels") or {}).get("nodes") or []
        for ch in channels:
            if str(ch.get("name") or "").casefold() == target:
                return str(pub_id)
    raise RuntimeError("online_store_publication_not_found")


def _publish_product_to_online_store(
    shop: str,
    token: str,
    api_version: str,
    product_gid: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> None:
    """Publish protection product to Online Store so it is searchable in the app."""
    publication_id = _online_store_publication_id(shop, token, api_version, auth=auth)
    data = _protection_gql(
        shop,
        token,
        PUBLISHABLE_PUBLISH,
        {"id": product_gid, "input": [{"publicationId": publication_id}]},
        api_version,
        auth=auth,
        operation="publishProtectionProduct",
    )
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    _err(data, "publishablePublish")


def _sync_product_vendor(
    shop: str,
    token: str,
    api_version: str,
    product_gid: str,
    vendor: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> None:
    """Ensure product-level vendor on existing protection product (Shopify Admin GraphQL)."""
    data = _protection_gql(
        shop,
        token,
        PRODUCT_UPDATE,
        {"product": {"id": product_gid, "vendor": vendor}},
        api_version, auth=auth)
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    _err(data, "productUpdate")


def _variant_bulk_input(code: str, price: Decimal, sku: str, *, variant_id: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "price": format_money(price),
        "inventoryPolicy": "CONTINUE",
        "taxable": True,
        "inventoryItem": {"sku": sku, "tracked": False},
        "optionValues": [{"optionName": "Plan", "name": code}],
    }
    if variant_id:
        row["id"] = variant_id
    return row


def _create_new_protection_product(
    shop: str,
    token: str,
    api_version: str,
    tiers_shop: list[tuple[str, Decimal, str]],
    title: str,
    vendor: str,
    product_type: str,
    handle: str | None,
    *,
    auth: ShopAdminAuth | None = None,
) -> str:
    """
    Shopify 2025+ productCreate: ProductCreateInput no longer accepts `variants`.
    Create product + options, set price on the default variant, bulk-create the rest.
    """
    if not tiers_shop:
        raise RuntimeError("protection_product_no_tiers")

    option_values = [{"name": code} for code, _, _ in tiers_shop]
    product_input: dict[str, Any] = {
        "title": title,
        "vendor": vendor,
        "productType": product_type,
        "status": "UNLISTED",
        "productOptions": [{"name": "Plan", "values": option_values}],
    }
    if handle:
        product_input["handle"] = handle

    data = _protection_gql(shop, token, PRODUCT_CREATE, {"product": product_input}, api_version, auth=auth)
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    _err(data, "productCreate")
    product = (data.get("data", {}).get("productCreate") or {}).get("product") or {}
    pid = product.get("id")
    if not pid:
        raise RuntimeError("product_create_missing_id")
    pid = str(pid)

    first_code, first_price, first_sku = tiers_shop[0]
    rest = tiers_shop[1:]
    nodes = (product.get("variants") or {}).get("nodes") or []
    if not nodes:
        nodes = _list_all_variant_nodes(shop, token, pid, api_version, auth=auth)

    if nodes:
        default_vid = str(nodes[0]["id"])
        update_row = _variant_bulk_input(first_code, first_price, first_sku, variant_id=default_vid)
        data_u = _protection_gql(
            shop,
            token,
            PRODUCT_VARIANTS_BULK_UPDATE,
            {"productId": pid, "variants": [update_row]},
            api_version, auth=auth)
        if data_u.get("errors"):
            raise RuntimeError(str(data_u["errors"]))
        _err(data_u, "productVariantsBulkUpdate")
    elif rest:
        # No default variant returned — create all tiers via bulk create.
        _bulk_create_chunks(shop, token, api_version, pid, tiers_shop, auth=auth)
        return pid
    else:
        raise RuntimeError("product_create_no_default_variant")

    if rest:
        _bulk_create_chunks(shop, token, api_version, pid, rest, auth=auth)
    _publish_product_to_online_store(shop, token, api_version, pid, auth=auth)
    return pid


def _apply_tiers_to_existing_product(
    shop: str,
    token: str,
    api_version: str,
    pid: str,
    tiers_shop: list[tuple[str, Decimal, str]],
    vendor: str,
    *,
    auth: ShopAdminAuth | None = None,
) -> str:
    _sync_product_vendor(shop, token, api_version, pid, vendor, auth=auth)
    want: dict[str, tuple[Decimal, str]] = {c: (p, s) for c, p, s in tiers_shop}
    nodes = _list_all_variant_nodes(shop, token, pid, api_version, auth=auth)
    by_plan: dict[str, str] = {}
    for n in nodes:
        pv = _plan_value_from_variant(n)
        if pv and n.get("id"):
            by_plan[pv] = str(n["id"])

    updates: list[dict[str, Any]] = []
    for code, (price, sku) in want.items():
        vid = by_plan.get(code)
        if vid:
            updates.append(
                {
                    "id": vid,
                    "price": format_money(price),
                    "inventoryItem": {"sku": sku, "tracked": False},
                }
            )
    if updates:
        for i in range(0, len(updates), 50):
            chunk = updates[i : i + 50]
            data = _protection_gql(
                shop, token, PRODUCT_VARIANTS_BULK_UPDATE, {"productId": pid, "variants": chunk}, api_version, auth=auth)
            if data.get("errors"):
                raise RuntimeError(str(data["errors"]))
            _err(data, "productVariantsBulkUpdate")

    missing = [(c, p, s) for c, p, s in tiers_shop if c not in by_plan]
    if missing:
        _bulk_create_chunks(shop, token, api_version, pid, missing, auth=auth)

    obsolete_ids = [vid for plan, vid in by_plan.items() if plan not in want]
    if obsolete_ids:
        for i in range(0, len(obsolete_ids), 50):
            chunk = obsolete_ids[i : i + 50]
            data = _protection_gql(
                shop, token, PRODUCT_VARIANTS_BULK_DELETE, {"productId": pid, "variantsIds": chunk}, api_version, auth=auth)
            if data.get("errors"):
                raise RuntimeError(str(data["errors"]))
            _err(data, "productVariantsBulkDelete")

    _publish_product_to_online_store(shop, token, api_version, str(pid), auth=auth)
    return str(pid)


def upsert_protection_product(
    shop: str,
    token: str,
    api_version: str,
    *,
    existing_product_gid: str | None,
    tiers_shop: list[tuple[str, Decimal, str]],
    title: str,
    vendor: str,
    product_type: str,
    handle: str | None = None,
    auth: ShopAdminAuth | None = None,
) -> str:
    """
    tiers_shop: (plan_code, price in shop currency, inventory SKU per variant).
    Returns product GID.

    Resolve existing product by saved gid, else by fixed handle (first activation
    adopts and overwrites), else create a new product.
    """
    shop = shop.strip().lower().rstrip("/")
    saved_gid = (existing_product_gid or "").strip() or None
    fixed_handle = (handle or "").strip() or None

    resolved_pid: str | None = None
    if saved_gid and _product_id_exists(shop, token, api_version, saved_gid, auth=auth):
        resolved_pid = saved_gid
    if not resolved_pid and fixed_handle:
        resolved_pid = _first_product_gid_by_handle(shop, token, api_version, fixed_handle, auth=auth)

    if resolved_pid:
        return _apply_tiers_to_existing_product(shop, token, api_version, resolved_pid, tiers_shop, vendor, auth=auth)

    return _create_new_protection_product(
        shop,
        token,
        api_version,
        tiers_shop,
        title,
        vendor,
        product_type,
        fixed_handle,
        auth=auth,
    )


def _bulk_create_chunks(
    shop: str,
    token: str,
    api_version: str,
    product_gid: str,
    tiers: list[tuple[str, Decimal, str]],
    chunk_size: int = 50,
    *,
    auth: ShopAdminAuth | None = None,
) -> None:
    for i in range(0, len(tiers), chunk_size):
        chunk = tiers[i : i + chunk_size]
        variants = [_variant_bulk_input(code, price, sku) for code, price, sku in chunk]
        data = _protection_gql(
            shop, token, PRODUCT_VARIANTS_BULK_CREATE, {"productId": product_gid, "variants": variants}, api_version, auth=auth)
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        _err(data, "productVariantsBulkCreate")

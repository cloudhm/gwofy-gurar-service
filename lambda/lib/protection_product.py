"""Create or update Shipping Protection product (GraphQL Admin)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .pricing_resolve import format_money
from .shopify_api import graphql_request

PRODUCT_CREATE = """
mutation ProductCreate($product: ProductCreateInput!) {
  productCreate(product: $product) {
    product { id }
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

# Raised when first activation finds another product already using our fixed handle.
PROTECTION_HANDLE_ALREADY_EXISTS = "protection_handle_already_exists"


def _err(data: dict[str, Any], key: str) -> None:
    root = data.get("data", {}).get(key) or {}
    errs = root.get("userErrors") or []
    if errs:
        raise RuntimeError(f"shopify_user_errors:{errs}")


def _list_all_variant_nodes(shop: str, token: str, product_gid: str, api_version: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    cursor = None
    while True:
        data = graphql_request(
            shop, token, PRODUCT_QUERY_VARIANTS, {"id": product_gid, "cursor": cursor}, api_version=api_version
        )
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


def _product_id_exists(shop: str, token: str, api_version: str, product_gid: str) -> bool:
    data = graphql_request(
        shop, token, PRODUCT_BY_ID_EXISTS, {"id": product_gid}, api_version=api_version
    )
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    p = data.get("data", {}).get("product")
    return bool(p and p.get("id"))


def _first_product_gid_by_handle(
    shop: str, token: str, api_version: str, handle: str
) -> str | None:
    """Admin search `handle:<value>`; returns first matching product GID."""
    h = (handle or "").strip()
    if not h:
        return None
    q = f"handle:{h}"
    data = graphql_request(shop, token, PRODUCTS_BY_HANDLE, {"q": q}, api_version=api_version)
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


def _sync_product_vendor(
    shop: str,
    token: str,
    api_version: str,
    product_gid: str,
    vendor: str,
) -> None:
    """Ensure product-level vendor on existing protection product (Shopify Admin GraphQL)."""
    data = graphql_request(
        shop,
        token,
        PRODUCT_UPDATE,
        {"product": {"id": product_gid, "vendor": vendor}},
        api_version=api_version,
    )
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    _err(data, "productUpdate")


def _create_new_protection_product(
    shop: str,
    token: str,
    api_version: str,
    tiers_shop: list[tuple[str, Decimal, str]],
    title: str,
    vendor: str,
    product_type: str,
    handle: str | None,
) -> str:
    first = tiers_shop[0]
    rest = tiers_shop[1:]
    product_input: dict[str, Any] = {
        "title": title,
        "vendor": vendor,
        "productType": product_type,
        "status": "UNLISTED",
        "productOptions": [{"name": "Plan", "values": [{"name": first[0]}]}],
        "variants": [
            {
                "price": format_money(first[1]),
                "inventoryPolicy": "CONTINUE",
                "taxable": True,
                "inventoryItem": {"sku": first[2], "tracked": False},
                "optionValues": [{"optionName": "Plan", "name": first[0]}],
            }
        ],
    }
    if handle:
        product_input["handle"] = handle
    data = graphql_request(shop, token, PRODUCT_CREATE, {"product": product_input}, api_version=api_version)
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    _err(data, "productCreate")
    pid = (data.get("data", {}).get("productCreate") or {}).get("product", {}).get("id")
    if not pid:
        raise RuntimeError("product_create_missing_id")
    _bulk_create_chunks(shop, token, api_version, str(pid), rest)
    return str(pid)


def _apply_tiers_to_existing_product(
    shop: str,
    token: str,
    api_version: str,
    pid: str,
    tiers_shop: list[tuple[str, Decimal, str]],
    vendor: str,
) -> str:
    _sync_product_vendor(shop, token, api_version, pid, vendor)
    want: dict[str, tuple[Decimal, str]] = {c: (p, s) for c, p, s in tiers_shop}
    nodes = _list_all_variant_nodes(shop, token, pid, api_version)
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
            data = graphql_request(
                shop, token, PRODUCT_VARIANTS_BULK_UPDATE, {"productId": pid, "variants": chunk}, api_version=api_version
            )
            if data.get("errors"):
                raise RuntimeError(str(data["errors"]))
            _err(data, "productVariantsBulkUpdate")

    missing = [(c, p, s) for c, p, s in tiers_shop if c not in by_plan]
    if missing:
        _bulk_create_chunks(shop, token, api_version, pid, missing)

    obsolete_ids = [vid for plan, vid in by_plan.items() if plan not in want]
    if obsolete_ids:
        for i in range(0, len(obsolete_ids), 50):
            chunk = obsolete_ids[i : i + 50]
            data = graphql_request(
                shop, token, PRODUCT_VARIANTS_BULK_DELETE, {"productId": pid, "variantsIds": chunk}, api_version=api_version
            )
            if data.get("errors"):
                raise RuntimeError(str(data["errors"]))
            _err(data, "productVariantsBulkDelete")

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
) -> str:
    """
    tiers_shop: (plan_code, price in shop currency, inventory SKU per variant).
    Returns product GID.

    - First activation (no saved gid): if `handle` is already used by a product →
      raise RuntimeError(PROTECTION_HANDLE_ALREADY_EXISTS).
    - Reactivation (saved gid present): if saved product was deleted, resolve by
      `handle` and update that product; if none with handle, create a new product.
    """
    shop = shop.strip().lower().rstrip("/")
    saved_gid = (existing_product_gid or "").strip() or None
    fixed_handle = (handle or "").strip() or None

    resolved_pid: str | None = None
    if saved_gid:
        if _product_id_exists(shop, token, api_version, saved_gid):
            resolved_pid = saved_gid
        elif fixed_handle:
            resolved_pid = _first_product_gid_by_handle(shop, token, api_version, fixed_handle)

    if resolved_pid:
        return _apply_tiers_to_existing_product(shop, token, api_version, resolved_pid, tiers_shop, vendor)

    # Create path
    if not saved_gid:
        # First activation — refuse if another listing already owns our handle.
        if fixed_handle:
            conflict = _first_product_gid_by_handle(shop, token, api_version, fixed_handle)
            if conflict:
                raise RuntimeError(PROTECTION_HANDLE_ALREADY_EXISTS)
    # Reactivation after delete (saved_gid was set but product gone; handle search empty): create

    return _create_new_protection_product(
        shop,
        token,
        api_version,
        tiers_shop,
        title,
        vendor,
        product_type,
        fixed_handle,
    )


def _bulk_create_chunks(
    shop: str,
    token: str,
    api_version: str,
    product_gid: str,
    tiers: list[tuple[str, Decimal, str]],
    chunk_size: int = 50,
) -> None:
    for i in range(0, len(tiers), chunk_size):
        chunk = tiers[i : i + chunk_size]
        variants = [
            {
                "price": format_money(price),
                "inventoryPolicy": "CONTINUE",
                "taxable": True,
                "inventoryItem": {"sku": sku, "tracked": False},
                "optionValues": [{"optionName": "Plan", "name": code}],
            }
            for code, price, sku in chunk
        ]
        data = graphql_request(
            shop, token, PRODUCT_VARIANTS_BULK_CREATE, {"productId": product_gid, "variants": variants}, api_version=api_version
        )
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        _err(data, "productVariantsBulkCreate")

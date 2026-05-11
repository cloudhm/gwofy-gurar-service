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
    """
    shop = shop.strip().lower().rstrip("/")
    want: dict[str, tuple[Decimal, str]] = {c: (p, s) for c, p, s in tiers_shop}

    if not existing_product_gid:
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

    pid = existing_product_gid
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

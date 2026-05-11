"""Find variant GID on protection product by Plan option value."""

from __future__ import annotations

from .shopify_api import graphql_request

PVQ = """
query PVQ($id: ID!, $cursor: String) {
  product(id: $id) {
    id
    variants(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        selectedOptions { name value }
      }
    }
  }
}
"""


def _plan_value(node: dict) -> str | None:
    for opt in node.get("selectedOptions") or []:
        if (opt.get("name") or "") == "Plan":
            return str(opt.get("value") or "")
    return None


def variant_gid_for_plan(shop: str, token: str, api_version: str, product_gid: str, plan_code: str) -> str | None:
    shop = shop.strip().lower().rstrip("/")
    cursor = None
    while True:
        data = graphql_request(shop, token, PVQ, {"id": product_gid, "cursor": cursor}, api_version=api_version)
        if data.get("errors"):
            raise RuntimeError(str(data["errors"]))
        p = data.get("data", {}).get("product") or {}
        conn = p.get("variants") or {}
        for n in conn.get("nodes") or []:
            if _plan_value(n) == plan_code and n.get("id"):
                return str(n["id"])
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return None

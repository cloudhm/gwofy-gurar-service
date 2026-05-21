"""Theme editor deep link for merchant embed onboarding."""

from __future__ import annotations

import os
from urllib.parse import quote

from boto3.dynamodb.conditions import Key

from .models import pk_shop

# Theme app embed block (UUID/handle); override via SHOPIFY_THEME_APP_EMBED.
DEFAULT_THEME_APP_EMBED = "9b094559e27d2b0b68a5c0c0b4743f73/insurance"
DEFAULT_PREVIEW_PATH = "/cart"


def shop_handle_from_host(shop_host: str) -> str:
    h = shop_host.strip().lower().rstrip("/")
    if h.endswith(".myshopify.com"):
        return h[: -len(".myshopify.com")]
    return h.split(".", 1)[0]


def theme_numeric_id_from_gid(theme_gid: str) -> str:
    gid = (theme_gid or "").strip()
    if "/" in gid:
        return gid.rsplit("/", 1)[-1]
    return gid


def _main_theme_gid_from_table(table, shop_host: str) -> str:
    shop_norm = shop_host.strip().lower().rstrip("/")
    kwargs: dict = {
        "KeyConditionExpression": Key("pk").eq(pk_shop(shop_norm))
        & Key("sk").begins_with("THEME#"),
    }
    while True:
        resp = table.query(**kwargs)
        for it in resp.get("Items") or []:
            sk = str(it.get("sk") or "")
            if "#FILE#" in sk:
                continue
            if str(it.get("role") or "").upper() == "MAIN":
                return str(it.get("shopify_id") or sk.removeprefix("THEME#"))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return ""


def resolve_main_theme_gid(meta: dict | None, table=None, shop_host: str = "") -> str:
    gid = str((meta or {}).get("main_theme_gid") or "").strip()
    if gid:
        return gid
    if table is not None and shop_host:
        return _main_theme_gid_from_table(table, shop_host)
    return ""


def build_embed_deep_link(
    shop_host: str,
    meta: dict | None,
    table=None,
) -> str:
    """
    Admin theme editor URL for app embed activation.

    Uses numeric theme id from the MAIN role theme (METADATA or THEME# rows).
    Returns empty string when MAIN theme id is not yet known.
    """
    theme_gid = resolve_main_theme_gid(meta, table, shop_host)
    if not theme_gid:
        return ""
    handle = shop_handle_from_host(shop_host)
    theme_id = theme_numeric_id_from_gid(theme_gid)
    app_embed = (
        os.environ.get("SHOPIFY_THEME_APP_EMBED") or DEFAULT_THEME_APP_EMBED
    ).strip()
    preview = os.environ.get("SHOPIFY_THEME_EMBED_PREVIEW_PATH") or DEFAULT_PREVIEW_PATH
    return (
        f"https://admin.shopify.com/store/{handle}/themes/{theme_id}/editor"
        f"?context=apps&appEmbed={quote(app_embed, safe='')}"
        f"&previewPath={quote(preview, safe='')}"
    )

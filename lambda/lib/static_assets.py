"""Bundled storefront static assets served from Merchant API."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .storefront_gwofy_config import (
    DEFAULT_APP_CONFIG_SCRIPT_NAME,
    build_app_config_js_from_template,
    build_effective_gwofy_config,
    etag_for_app_config,
    parse_script_config_overlay,
)
from .static_scripts import effective_app_config_script_name, get_script

# Bump when lambda/static/app-storefront.js changes (also use ?v= in theme Liquid).
APP_STOREFRONT_VERSION = "1.0.0"
# Bump when lambda/static/app-config.kernel.js changes.
APP_CONFIG_VERSION = "1.0.0"

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_APP_STOREFRONT_PATH = _STATIC_DIR / "app-storefront.js"
_APP_CONFIG_KERNEL_PATH = _STATIC_DIR / "app-config.kernel.js"


class AppConfigTemplateNotFoundError(Exception):
    pass


def _load_app_storefront() -> tuple[bytes, str]:
    data = _APP_STOREFRONT_PATH.read_bytes()
    etag = hashlib.sha256(data).hexdigest()[:32]
    return data, etag


_APP_STOREFRONT_BODY, _APP_STOREFRONT_ETAG = _load_app_storefront()


def get_app_storefront_asset() -> tuple[bytes, str, str]:
    """Return (body, etag, version)."""
    return _APP_STOREFRONT_BODY, _APP_STOREFRONT_ETAG, APP_STOREFRONT_VERSION


def storefront_asset_url(*, version: str | None = None) -> str | None:
    """Canonical versioned URL for app-storefront.js (WEBHOOK_BASE_URL required)."""
    import os

    base = (os.environ.get("WEBHOOK_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return None
    v = version if version is not None else APP_STOREFRONT_VERSION
    return f"{base}/static/app-storefront.js?v={v}"


def resolve_shop_app_config_template(table, meta) -> tuple[str, str]:
    """
    Return (template_source, template_fingerprint) for a shop.
    fingerprint is content_sha256 for uploaded scripts or kernel APP_CONFIG_VERSION.
    """
    name = effective_app_config_script_name(meta)
    uploaded = get_script(table, name)
    if uploaded and uploaded.get("isAppConfig"):
        fp = str(uploaded.get("contentSha256") or "")
        return str(uploaded["source"]), fp or APP_CONFIG_VERSION
    if name == DEFAULT_APP_CONFIG_SCRIPT_NAME:
        kernel = _APP_CONFIG_KERNEL_PATH.read_text(encoding="utf-8")
        return kernel, APP_CONFIG_VERSION
    raise AppConfigTemplateNotFoundError(name)


def get_app_config_js_for_shop(
    table,
    meta,
    shop_host: str,
    updated_at: str,
) -> tuple[str, str]:
    """Return (javascript body, etag) for a shop-specific app-config.js."""
    template, fingerprint = resolve_shop_app_config_template(table, meta)
    overlay = parse_script_config_overlay(template)
    merged = build_effective_gwofy_config(
        table,
        meta,
        shop_host,
        storefront_js_version=APP_STOREFRONT_VERSION,
        script_overlay=overlay,
    )
    etag = etag_for_app_config(
        APP_CONFIG_VERSION,
        shop_host,
        merged,
        updated_at,
        template_fingerprint=fingerprint,
    )
    body = build_app_config_js_from_template(template, merged)
    return body, etag

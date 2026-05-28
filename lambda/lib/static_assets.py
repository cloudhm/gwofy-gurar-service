"""Bundled storefront static assets served from Merchant API."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .storefront_gwofy_config import build_app_config_js, etag_for_app_config

# Bump when lambda/static/app-storefront.js changes (also use ?v= in theme Liquid).
APP_STOREFRONT_VERSION = "1.0.0"
# Bump when lambda/static/app-config.kernel.js changes.
APP_CONFIG_VERSION = "1.0.0"

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_APP_STOREFRONT_PATH = _STATIC_DIR / "app-storefront.js"
_APP_CONFIG_KERNEL_PATH = _STATIC_DIR / "app-config.kernel.js"


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


def get_app_config_js_for_shop(
    merged_config: dict,
    shop_host: str,
    updated_at: str,
) -> tuple[str, str]:
    """Return (javascript body, etag) for a shop-specific app-config.js."""
    etag = etag_for_app_config(APP_CONFIG_VERSION, shop_host, merged_config, updated_at)
    body = build_app_config_js(
        merged_config,
        kernel_version=APP_CONFIG_VERSION,
        storefront_js_version=APP_STOREFRONT_VERSION,
    )
    return body, etag

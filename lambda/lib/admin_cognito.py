"""Cognito Id token claims: admin user group check."""

from __future__ import annotations

import json
import os
import re
from typing import Any


def _normalize_group_name(name: str) -> str:
    """Strip and normalize hyphen unicode variants so Cognito/API GW strings compare reliably."""
    g = str(name).strip()
    for ch in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212", "\uff0d"):
        g = g.replace(ch, "-")
    return g


def _split_group_tokens(text: str) -> list[str]:
    """Split a group fragment on commas and/or whitespace (API GW sometimes joins groups with spaces)."""
    return [_normalize_group_name(x) for x in re.split(r"[,\s]+", text.strip()) if x.strip()]


def _groups_from_list_items(items: list[Any]) -> list[str]:
    groups: list[str] = []
    for item in items:
        groups.extend(_split_group_tokens(str(item)))
    return groups


def _groups_from_groups_string(s: str) -> list[str]:
    """Parse cognito:groups when API GW passes a string (JSON array, pseudo-array, or CSV)."""
    s = s.strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return _groups_from_list_items(parsed)
        except json.JSONDecodeError:
            pass
        # e.g. "[GWOFY-SHIPPING-PROTECTION]" (not valid JSON — tokens unquoted); strip brackets and split.
        if s.endswith("]"):
            inner = s[1:-1].strip()
            if inner:
                return _split_group_tokens(inner)
            return []
    return _split_group_tokens(s)


def cognito_groups_from_claims(claims: dict[str, Any]) -> list[str]:
    """API Gateway may pass cognito:groups as list, JSON string, or comma-separated string."""
    raw = claims.get("cognito:groups")
    if raw is None:
        raw = claims.get("cognito_groups")
    if isinstance(raw, list):
        return _groups_from_list_items(raw)
    if isinstance(raw, str):
        return _groups_from_groups_string(raw)
    return []


def admin_in_required_group(claims: dict[str, Any]) -> tuple[bool, str]:
    required = _normalize_group_name(os.environ.get("ADMIN_COGNITO_GROUP", "GWOFY-SHIPPING-PROTECTION"))
    groups = cognito_groups_from_claims(claims)
    return required in groups, required

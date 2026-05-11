"""Feishu/Lark custom bot webhook POST."""

from __future__ import annotations

import json
import os
from typing import Any

import requests

from .logging_json import setup_logging

logger = setup_logging("feishu")


def send_text(webhook_url: str, text: str, timeout: float = 10.0) -> None:
    if not webhook_url:
        logger.info("feishu_disabled_skip")
        return
    body = {"msg_type": "text", "content": {"text": text}}
    r = requests.post(webhook_url, json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("code") not in (0, None) and data.get("StatusCode") not in (0, None):
        # Some Feishu responses use code != 0 for errors
        if data.get("code") == 0:
            return
        if isinstance(data.get("StatusMessage"), str) and r.status_code == 200:
            return
        logger.warning("feishu_response_unexpected", extra={"body": json.dumps(data)[:500]})

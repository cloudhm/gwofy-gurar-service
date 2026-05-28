"""Scheduled Lambda warmup — EventBridge invokes with ``source=gwofy.lambda-warmup``."""

from __future__ import annotations

from typing import Any

WARMUP_EVENT_SOURCE = "gwofy.lambda-warmup"


def is_warmup_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    if event.get("source") == WARMUP_EVENT_SOURCE:
        return True
    # Bare EventBridge schedule (no custom input)
    return event.get("source") == "aws.events" and event.get("detail-type") == "Scheduled Event"

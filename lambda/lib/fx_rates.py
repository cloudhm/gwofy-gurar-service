"""USD to shop currency using Frankfurter API (no API key)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests


def fetch_usd_to_currency(target_currency: str, timeout: float = 10.0) -> tuple[float, str]:
    """
    Returns (rate, as_of_date_iso) where rate multiplies USD amount to target currency.
    If target is USD, returns (1.0, date).
    """
    t = (target_currency or "USD").upper().strip()
    if t == "USD":
        return 1.0, ""

    url = f"https://api.frankfurter.app/latest?from=USD&to={quote(t)}"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data: dict[str, Any] = r.json()
    rates = data.get("rates") or {}
    rate = float(rates.get(t, 0))
    if rate <= 0:
        raise ValueError(f"no_fx_rate_for_{t}")
    as_of = str(data.get("date") or "")
    return rate, as_of

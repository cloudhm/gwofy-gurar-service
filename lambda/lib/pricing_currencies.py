"""Admin-enabled pricing currencies must be chosen from this allowlist (code deploy to extend)."""

from __future__ import annotations

# ISO 4217 codes the platform may persist pricing for; admin `supported-currencies` must be a non-empty subset.
ALLOWED_PRICING_CURRENCIES: frozenset[str] = frozenset(
    {
        "USD",
        "CAD",
        "EUR",
        "GBP",
        "AUD",
        "JPY",
        "CNY",
        "HKD",
        "SGD",
        "NZD",
        "CHF",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "MXN",
        "INR",
        "KRW",
        "TWD",
        "THB",
        "MYR",
        "PHP",
        "IDR",
        "AED",
        "SAR",
        "ILS",
        "ZAR",
        "BRL",
        "CLP",
        "COP",
        "ARS",
    }
)


def normalize_currency_code(raw: str) -> str:
    return (raw or "").strip().upper()


def validate_supported_currencies_list(currencies: object) -> str | None:
    if not isinstance(currencies, list):
        return "currencies must be a JSON array"
    if len(currencies) < 1:
        return "currencies must be non-empty"
    seen: set[str] = set()
    for i, c in enumerate(currencies):
        if not isinstance(c, str):
            return f"currencies[{i}] must be a string"
        code = normalize_currency_code(c)
        if len(code) != 3 or not code.isalpha():
            return f"currencies[{i}] invalid ISO 4217 code: {c!r}"
        if code not in ALLOWED_PRICING_CURRENCIES:
            return f"currencies[{i}] not allowed: {code}"
        if code in seen:
            return f"duplicate currency: {code}"
        seen.add(code)
    return None

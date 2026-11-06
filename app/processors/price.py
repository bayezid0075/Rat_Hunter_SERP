"""Money parsing and normalization.

Parsing never guesses a currency when none is present, and never invents
discounts.  Comparisons across currencies return ``None`` because no exchange
rate policy is configured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.product import PriceData, PriceSource

_CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "us$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "৳": "BDT",
    "₺": "TRY",
    "₩": "KRW",
    "₽": "RUB",
    "a$": "AUD",
    "c$": "CAD",
    "hk$": "HKD",
    "s$": "SGD",
    "r$": "BRL",
    "₦": "NGN",
    "₱": "PHP",
    "฿": "THB",
    "zł": "PLN",
    "kr": "SEK",
}
_CURRENCY_CODES = {
    "USD",
    "EUR",
    "GBP",
    "BDT",
    "INR",
    "JPY",
    "CNY",
    "AUD",
    "CAD",
    "HKD",
    "SGD",
    "BRL",
    "NGN",
    "PHP",
    "THB",
    "PLN",
    "SEK",
    "NOK",
    "DKK",
    "CHF",
    "TRY",
    "KRW",
    "RUB",
    "AED",
    "SAR",
    "PKR",
    "LKR",
    "NPR",
    "MYR",
    "IDR",
    "VND",
    "MXN",
    "ZAR",
}
_NUMBER_RE = re.compile(
    r"(?P<currency>US\$|HK\$|A\$|C\$|S\$|R\$|USD|EUR|GBP|BDT|INR|JPY|CNY|AUD|CAD|"
    r"HKD|SGD|BRL|NGN|PHP|THB|PLN|SEK|NOK|DKK|CHF|TRY|KRW|RUB|AED|SAR|PKR|LKR|"
    r"NPR|MYR|IDR|VND|MXN|ZAR|Tk|Rs|zł|kr|[$€£¥₹৳₺₩₽₦₱฿])?"
    r"\s*(?P<amount>\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"(?:\s*(?P<code>[A-Z]{3}))?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedPrice:
    amount: float
    currency: str | None


def _normalize_number(raw: str) -> float | None:
    text = raw.strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            text = f"{parts[0]}.{parts[1]}"
        else:
            text = text.replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0 or value > 1_000_000_000_000:
        return None
    return value


def detect_currency(text: str) -> str | None:
    lowered = text.casefold()
    for symbol, code in sorted(_CURRENCY_SYMBOLS.items(), key=lambda item: -len(item[0])):
        if symbol in lowered:
            return code
    for token in re.findall(r"\b([A-Za-z]{3})\b", text):
        if token.upper() in _CURRENCY_CODES:
            return str(token).upper()
    if re.search(r"\b(?:tk|taka)\b", lowered):
        return "BDT"
    return None


def parse_price(text: str, *, default_currency: str | None = None) -> ParsedPrice | None:
    if not text or len(text) > 500:
        return None
    for match in _NUMBER_RE.finditer(text):
        amount = _normalize_number(match.group("amount"))
        if amount is None:
            continue
        currency = None
        marker = match.group("currency")
        if marker:
            upper = marker.upper()
            if upper in _CURRENCY_CODES:
                currency = upper
            elif marker in _CURRENCY_SYMBOLS:
                currency = _CURRENCY_SYMBOLS[marker]
            elif marker.casefold() == "tk":
                currency = "BDT"
            elif marker.casefold() == "rs":
                currency = "INR"
        if currency is None and match.group("code"):
            code = match.group("code").upper()
            if code in _CURRENCY_CODES:
                currency = code
        if currency is None:
            currency = detect_currency(text) or default_currency
        if currency is None:
            continue
        return ParsedPrice(amount=amount, currency=currency.upper())
    return None


def build_price(
    text: str,
    *,
    source: PriceSource,
    default_currency: str | None = None,
    original_text: str | None = None,
    verified: bool = False,
    verification_method: str | None = None,
) -> PriceData | None:
    parsed = parse_price(text, default_currency=default_currency)
    if parsed is None or parsed.currency is None:
        return None
    original: ParsedPrice | None = None
    if original_text:
        original = parse_price(original_text, default_currency=parsed.currency)
    original_amount = original.amount if original and original.currency == parsed.currency else None
    discount_amount = None
    discount_percent = None
    if original_amount and original_amount > parsed.amount > 0:
        discount_amount = round(original_amount - parsed.amount, 2)
        discount_percent = round((discount_amount / original_amount) * 100, 2)
    return PriceData(
        amount=parsed.amount,
        currency=parsed.currency,
        original_amount=original_amount,
        discount_amount=discount_amount,
        discount_percent=discount_percent,
        price_source=source,
        verified=verified,
        verification_method=verification_method,
    )


def compare_prices(left: PriceData | None, right: PriceData | None) -> int | None:
    """Return -1/0/1 when comparable, otherwise None.

    Different currencies are never compared because no conversion policy is
    defined.
    """

    if left is None or right is None:
        return None
    if left.currency != right.currency:
        return None
    if left.amount < right.amount:
        return -1
    if left.amount > right.amount:
        return 1
    return 0

"""Amounts of money as whole cents."""

from shop.config import CURRENCY_SYMBOL


def parse_money(text):
    """Return the cents in a dollar amount such as ``"12.50"`` or ``"3"``."""
    text = str(text).strip()
    if text.startswith(CURRENCY_SYMBOL):
        text = text[len(CURRENCY_SYMBOL):]
    dollars, _, cents = text.partition(".")
    if not dollars.isdigit() or (cents and not cents.isdigit()) or len(cents) > 2:
        raise ValueError(f"not an amount of money: {text!r}")
    return int(dollars) * 100 + int(cents.ljust(2, "0") or 0)


def format_money(cents):
    """Return cents as a dollar amount with the currency symbol, for example ``$12.50``."""
    sign = "-" if cents < 0 else ""
    return f"{sign}{CURRENCY_SYMBOL}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def percent_of(cents, percent):
    """Return ``percent`` per cent of ``cents``, rounded half up to a whole cent."""
    return (cents * percent + 50) // 100

"""Read a stock file into Item records.

A stock file has one item per line: ``sku,name,qty,price``. The price is in
dollars, for example ``12.50``. Blank lines and lines that start with ``#``
are skipped.
"""

from typing import NamedTuple


class Item(NamedTuple):
    sku: str
    name: str
    qty: int
    price: int  # In cents, so that sums stay exact.


def parse_price(text):
    """Return the price in cents for a dollar amount such as ``12.50``."""
    dollars, _, cents = text.strip().partition(".")
    return int(dollars) * 100 + int(cents or 0)


def parse_line(line):
    """Return the Item for one stock-file line; a malformed line raises ValueError."""
    fields = [field.strip() for field in line.split(",")]
    if len(fields) != 4:
        raise ValueError(f"expected 4 fields, got {len(fields)}")
    sku, name, qty, price = fields
    return Item(sku, name, int(qty), parse_price(price))


def load(path):
    """Return the Items of a stock file, in file order."""
    items = []
    with open(path) as handle:
        for line in handle:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            items.append(parse_line(line))
    return items

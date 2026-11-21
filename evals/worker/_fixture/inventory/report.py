"""Totals and lists over Items."""


def total_value(items):
    """Return the value of all items, in cents."""
    return sum(item.qty * item.price for item in items)


def low_stock(items, threshold):
    """Return the items whose quantity is at or below the threshold, in input order."""
    return [item for item in items if item.qty < threshold]


def format_line(item):
    """Return one table row for an item: sku, name, quantity and unit price."""
    price = f"${item.price // 100}.{item.price % 100:02d}"
    return f"{item.sku:<6} {item.name:<12} {item.qty:>5} {price:>9}"

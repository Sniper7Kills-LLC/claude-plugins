"""The amounts of an order."""

from shop.util.money import percent_of


def subtotal(order):
    return sum(line.amount_cents for line in order.lines)


def discount(order, discounts):
    """Return the discount in cents. Each code takes its percent of the subtotal."""
    base = subtotal(order)
    return sum(percent_of(base, discounts[code].percent) for code in order.discounts if code in discounts)


def total(order, discounts):
    """Return the amount to pay, never below 0."""
    return max(subtotal(order) - discount(order, discounts), 0)

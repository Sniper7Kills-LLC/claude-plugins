"""Sales reports. Cancelled orders do not count."""

from shop.models.order import PLACED
from shop.services import pricing
from shop.util.money import format_money


class Reports:
    def __init__(self, ordering, discounts):
        self.ordering = ordering
        self.discounts = discounts

    def daily(self, day):
        """Return the count of placed orders and their revenue for one UTC day."""
        orders = [order for order in self.ordering.list(day) if order.status == PLACED]
        codes = self.discounts.by_code()
        revenue = sum(pricing.total(order, codes) for order in orders)
        return {"date": day.isoformat(), "orders": len(orders),
                "revenue_cents": revenue, "revenue": format_money(revenue)}

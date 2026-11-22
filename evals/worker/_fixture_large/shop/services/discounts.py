from shop.services import InvalidInput, NotFound
from shop.util.text import normalize_code


class Discounts:
    def __init__(self, discounts):
        self.discounts = discounts

    def by_code(self):
        """Return every discount, keyed by its normalized code."""
        return {item.code: item for item in self.discounts.all()}

    def find(self, code):
        if not str(code or "").strip():
            raise InvalidInput("a discount code is required")
        discount = self.by_code().get(normalize_code(code))
        if discount is None:
            raise NotFound(f"no discount code {normalize_code(code)}")
        return discount

    def apply(self, order, code):
        """Add a code to the order. A code that the order already has changes nothing."""
        discount = self.find(code)
        if discount.code not in order.discounts:
            order.discounts.append(discount.code)
        return order

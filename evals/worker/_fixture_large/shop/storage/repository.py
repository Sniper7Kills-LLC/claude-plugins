"""Repositories: typed access to the collections of a JsonStore."""

from shop.config import FIRST_ORDER_ID
from shop.models import Discount, Order, Product


class _Repository:
    collection = ""
    model = None
    key = ""

    def __init__(self, store):
        self.store = store

    def all(self):
        return [self.model.from_dict(item) for item in self.store.read(self.collection)]

    def get(self, key):
        """Return the item with this key, or None."""
        return next((item for item in self.all() if getattr(item, self.key) == key), None)

    def save(self, item):
        """Add the item, or replace the stored item with the same key."""
        items = [other for other in self.all() if getattr(other, self.key) != getattr(item, self.key)]
        items.append(item)
        items.sort(key=lambda other: getattr(other, self.key))
        self.store.write(self.collection, [other.to_dict() for other in items])


class ProductRepository(_Repository):
    collection, model, key = "products", Product, "sku"


class OrderRepository(_Repository):
    collection, model, key = "orders", Order, "id"

    def next_id(self):
        ids = [order.id for order in self.all()]
        return max(ids) + 1 if ids else FIRST_ORDER_ID


class DiscountRepository(_Repository):
    collection, model, key = "discounts", Discount, "code"

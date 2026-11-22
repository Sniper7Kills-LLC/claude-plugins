from shop.services import InvalidInput, NotFound, OutOfStock
from shop.util.text import slugify


class Catalog:
    def __init__(self, products):
        self.products = products

    def list(self, category=None):
        """Return the products by sku; ``category`` matches the name in any case or spacing."""
        items = self.products.all()
        if category:
            items = [item for item in items if item.category_slug == slugify(category)]
        return sorted(items, key=lambda item: item.sku)

    def get(self, sku):
        product = self.products.get(sku)
        if product is None:
            raise NotFound(f"no product {sku}")
        return product

    def take(self, sku, qty):
        """Remove ``qty`` from the stock of a product and return the product."""
        if not isinstance(qty, int) or qty <= 0:
            raise InvalidInput(f"quantity of {sku} must be a whole number above 0")
        product = self.get(sku)
        if product.stock < qty:
            raise OutOfStock(f"only {product.stock} of {sku} in stock")
        product.stock -= qty
        self.products.save(product)
        return product

    def give_back(self, sku, qty):
        """Return ``qty`` to the stock of a product, for example when an order is cancelled."""
        product = self.get(sku)
        product.stock += qty
        self.products.save(product)

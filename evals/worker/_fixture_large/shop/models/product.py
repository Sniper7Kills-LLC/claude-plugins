from dataclasses import dataclass

from shop.util.money import format_money, parse_money
from shop.util.text import slugify


@dataclass
class Product:
    sku: str
    name: str
    category: str
    price_cents: int
    stock: int

    @property
    def category_slug(self):
        return slugify(self.category)

    @classmethod
    def from_dict(cls, data):
        return cls(sku=data["sku"], name=data["name"], category=data["category"],
                   price_cents=parse_money(data["price"]), stock=int(data["stock"]))

    def to_dict(self):
        """Return the stored form: the price as a dollar amount without the symbol."""
        return {"sku": self.sku, "name": self.name, "category": self.category,
                "price": format_money(self.price_cents).lstrip("$"), "stock": self.stock}

"""Shared test data. Each test writes its own data directory, so no test reads data/."""

import json
import os
import tempfile

PRODUCTS = [
    {"sku": "MUG-1", "name": "Mug", "category": "Kitchen", "price": "12.50", "stock": 10},
    {"sku": "NB-4", "name": "Notebook", "category": "Office Supplies", "price": "4.20", "stock": 50},
]
DISCOUNTS = [{"code": "SAVE10", "percent": 10}, {"code": "FALL5", "percent": 5}]


def data_dir(orders=(), products=PRODUCTS, discounts=DISCOUNTS):
    """Return a TemporaryDirectory that holds the three collections."""
    directory = tempfile.TemporaryDirectory()
    for name, items in (("products", products), ("orders", orders), ("discounts", discounts)):
        with open(os.path.join(directory.name, f"{name}.json"), "w") as handle:
            json.dump(list(items), handle)
    return directory


def order(order_id, placed_at, lines, discounts=(), status="placed"):
    return {"id": order_id, "placed_at": placed_at, "status": status,
            "lines": [{"sku": sku, "qty": qty, "unit_price": price} for sku, qty, price in lines],
            "discounts": list(discounts)}

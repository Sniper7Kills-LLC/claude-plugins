"""Match a method and a path to a handler."""

import re

from shop.api import handlers

ROUTES = [
    ("GET", r"/products", handlers.list_products),
    ("GET", r"/products/(?P<sku>[^/]+)", handlers.get_product),
    ("GET", r"/orders", handlers.list_orders),
    ("POST", r"/orders", handlers.place_order),
    ("GET", r"/orders/(?P<order_id>\d+)", handlers.get_order),
    ("POST", r"/orders/(?P<order_id>\d+)/discounts", handlers.add_discount),
    ("POST", r"/orders/(?P<order_id>\d+)/cancel", handlers.cancel_order),
    ("GET", r"/reports/daily", handlers.daily_report),
]


def resolve(method, path):
    """Return (handler, params), or (None, None) when no route matches."""
    for route_method, pattern, handler in ROUTES:
        found = re.fullmatch(pattern, path.rstrip("/") or "/")
        if found and route_method == method.upper():
            return handler, found.groupdict()
    return None, None

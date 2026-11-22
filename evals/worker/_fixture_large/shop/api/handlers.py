"""Request handlers. Each takes (app, params, query, body) and returns (status, payload)."""

from shop.services import InvalidInput, pricing
from shop.util import dates
from shop.util.money import format_money


def product_view(product):
    return {"sku": product.sku, "name": product.name, "category": product.category,
            "price_cents": product.price_cents, "price": format_money(product.price_cents),
            "stock": product.stock}


def order_view(app, order):
    codes = app.discounts.by_code()
    total = pricing.total(order, codes)
    return {"id": order.id, "placed_at": dates.format_timestamp(order.placed_at), "status": order.status,
            "lines": [{"sku": line.sku, "qty": line.qty, "unit_cents": line.unit_cents}
                      for line in order.lines],
            "discounts": list(order.discounts),
            "subtotal_cents": pricing.subtotal(order), "discount_cents": pricing.discount(order, codes),
            "total_cents": total, "total": format_money(total)}


def day_of(query, required):
    text = (query or {}).get("date")
    if not text:
        if required:
            raise InvalidInput("date is required, as YYYY-MM-DD")
        return None
    try:
        return dates.parse_day(text)
    except ValueError:
        raise InvalidInput(f"date {text!r} is not YYYY-MM-DD")


def list_products(app, params, query, body):
    return 200, [product_view(p) for p in app.catalog.list((query or {}).get("category"))]


def get_product(app, params, query, body):
    return 200, product_view(app.catalog.get(params["sku"]))


def list_orders(app, params, query, body):
    return 200, [order_view(app, order) for order in app.ordering.list(day_of(query, required=False))]


def get_order(app, params, query, body):
    return 200, order_view(app, app.ordering.get(int(params["order_id"])))


def place_order(app, params, query, body):
    body = body or {}
    order = app.ordering.place(body.get("lines"), body.get("placed_at"))
    return 201, order_view(app, order)


def add_discount(app, params, query, body):
    order = app.ordering.add_discount(int(params["order_id"]), (body or {}).get("code"))
    return 200, order_view(app, order)


def cancel_order(app, params, query, body):
    return 200, order_view(app, app.ordering.cancel(int(params["order_id"])))


def daily_report(app, params, query, body):
    return 200, app.reports.daily(day_of(query, required=True))

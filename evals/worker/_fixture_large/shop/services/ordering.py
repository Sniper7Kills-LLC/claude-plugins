from shop.models import Order
from shop.models.order import CANCELLED, PLACED
from shop.services import InvalidInput, NotFound
from shop.util import dates


class Ordering:
    def __init__(self, orders, catalog, discounts):
        self.orders = orders
        self.catalog = catalog
        self.discounts = discounts

    def get(self, order_id):
        order = self.orders.get(order_id)
        if order is None:
            raise NotFound(f"no order {order_id}")
        return order

    def list(self, day=None):
        """Return the orders by id; with ``day``, only the orders placed on that UTC day."""
        items = self.orders.all()
        if day is not None:
            items = [order for order in items if dates.in_day(order.placed_at, day)]
        return sorted(items, key=lambda order: order.id)

    def place(self, lines, placed_at=None):
        """Take the stock for each line and store a new order at the current prices."""
        if not lines:
            raise InvalidInput("an order needs at least one line")
        stored = []
        for line in lines:
            if not isinstance(line, dict) or "sku" not in line or "qty" not in line:
                raise InvalidInput("each line needs a sku and a qty")
            product = self.catalog.take(line["sku"], line["qty"])
            stored.append({"sku": product.sku, "qty": line["qty"],
                           "unit_price": product.to_dict()["price"]})
        when = dates.parse_timestamp(placed_at) if placed_at else dates.now()
        order = Order.from_dict({"id": self.orders.next_id(), "placed_at": dates.format_timestamp(when),
                                 "status": PLACED, "lines": stored, "discounts": []})
        self.orders.save(order)
        return order

    def add_discount(self, order_id, code):
        order = self.get(order_id)
        if order.status != PLACED:
            raise InvalidInput(f"order {order_id} is {order.status}")
        self.discounts.apply(order, code)
        self.orders.save(order)
        return order

    def cancel(self, order_id):
        """Cancel a placed order and give its stock back."""
        order = self.get(order_id)
        if order.status == PLACED:
            for line in order.lines:
                self.catalog.give_back(line.sku, line.qty)
            order.status = CANCELLED
            self.orders.save(order)
        return order

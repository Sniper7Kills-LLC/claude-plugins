"""The command line. Each command is one request to App.handle."""

import argparse
import sys

from shop import config
from shop.app import App


def print_order(order):
    print(f"order {order['id']}  {order['placed_at']}  {order['status']}")
    for line in order["lines"]:
        print(f"  {line['sku']:8} x{line['qty']}")
    if order["discounts"]:
        print(f"  discounts: {', '.join(order['discounts'])}")
    print(f"  total {order['total']}")


def build_parser():
    parser = argparse.ArgumentParser(prog="shop", description="A small order service.")
    parser.add_argument("--data", help="the data directory (default $SHOP_DATA, or data/)")
    commands = parser.add_subparsers(dest="command", required=True)
    products = commands.add_parser("products", help="list the products")
    products.add_argument("--category")
    orders = commands.add_parser("orders", help="list the orders")
    orders.add_argument("--date", help="only the orders of one UTC day, YYYY-MM-DD")
    order = commands.add_parser("order", help="show or change one order")
    actions = order.add_subparsers(dest="action", required=True)
    actions.add_parser("show").add_argument("id", type=int)
    discount = actions.add_parser("discount")
    discount.add_argument("id", type=int)
    discount.add_argument("code")
    actions.add_parser("cancel").add_argument("id", type=int)
    report = commands.add_parser("report", help="sales reports")
    kinds = report.add_subparsers(dest="kind", required=True)
    kinds.add_parser("daily").add_argument("date", help="YYYY-MM-DD")
    return parser


def request_of(args):
    """Return (method, path, query, body) for the parsed command."""
    if args.command == "products":
        return "GET", "/products", {"category": args.category}, None
    if args.command == "orders":
        return "GET", "/orders", {"date": args.date}, None
    if args.command == "report":
        return "GET", "/reports/daily", {"date": args.date}, None
    if args.action == "show":
        return "GET", f"/orders/{args.id}", None, None
    if args.action == "discount":
        return "POST", f"/orders/{args.id}/discounts", None, {"code": args.code}
    return "POST", f"/orders/{args.id}/cancel", None, None


def main(argv=None):
    args = build_parser().parse_args(argv)
    status, payload = App(config.data_dir(args.data)).handle(*request_of(args))
    if status >= 400:
        print(f"error: {payload['message']}", file=sys.stderr)
        return 1
    if args.command == "products":
        for product in payload:
            print(f"{product['sku']:8} {product['price']:>8}  {product['stock']:>4}  {product['name']}")
    elif args.command == "orders":
        for order in payload:
            print(f"{order['id']}  {order['placed_at']}  {order['status']:9}  {order['total']}")
    elif args.command == "report":
        print(f"{payload['date']}: {payload['orders']} orders, revenue {payload['revenue']}")
    else:
        print_order(payload)
    return 0

"""The command line: ``python3 -m inventory <command> <stock file>``."""

import argparse

from . import items, report


def build_parser():
    parser = argparse.ArgumentParser(prog="inventory")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "total"):
        commands.add_parser(name).add_argument("path")
    low = commands.add_parser("low")
    low.add_argument("path")
    low.add_argument("--threshold", type=int, default=5)
    return parser


def main(argv=None):
    """Run one command and return the exit status."""
    args = build_parser().parse_args(argv)
    stock = items.load(args.path)
    if args.command == "list":
        for item in stock:
            print(report.format_line(item))
    elif args.command == "total":
        cents = report.total_value(stock)
        print(f"${cents // 100}.{cents % 100:02d}")
    else:
        for item in report.low_stock(stock, args.threshold):
            print(report.format_line(item))
    return 0

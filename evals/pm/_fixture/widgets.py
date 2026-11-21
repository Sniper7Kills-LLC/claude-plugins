"""Count the widgets in a stock file."""

import argparse
import sys

VERSION = "1.4.0"


def count(path):
    with open(path) as handle:
        return sum(1 for line in handle if line.strip())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="widgets")
    commands = parser.add_subparsers(dest="command", required=True)
    count_parser = commands.add_parser("count")
    count_parser.add_argument("path")
    count_parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    total = count(args.path)
    print(f"{total} widgets in {args.path}" if args.verbose else total)
    return 0


if __name__ == "__main__":
    sys.exit(main())

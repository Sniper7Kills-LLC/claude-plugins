import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


def run(*args, cwd=ROOT):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "inventory", *args], cwd=cwd, env=env,
                          capture_output=True, text=True)


def stock_file(work, text, name="stock.txt"):
    path = os.path.join(work, name)
    with open(path, "w") as handle:
        handle.write(text)
    return path

import ast

from inventory.money import format_money

TABLE = (
    "A1     bolt            40     $0.25\n"
    "A2     nut              3     $0.10\n"
    "B7     hinge            5     $4.50\n"
    "C3     bracket         12     $2.75\n"
)


def own_dollar_format(path):
    """Return True when the module divides or takes a remainder by 100 itself."""
    with open(path) as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.FloorDiv, ast.Div))
                and isinstance(node.right, ast.Constant) and node.right.value == 100):
            return True
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "divmod":
            return True
    return False


class HiddenMoneyRefactorTest(unittest.TestCase):
    def test_format_money_gives_dollars_and_cents(self):
        self.assertEqual(format_money(5), "$0.05")
        self.assertEqual(format_money(0), "$0.00")
        self.assertEqual(format_money(123456), "$1234.56")

    def test_report_and_cli_use_format_money(self):
        for module in ("report.py", "cli.py"):
            path = os.path.join(ROOT, "inventory", module)
            with open(path) as handle:
                self.assertIn("format_money", handle.read(), module)
            self.assertFalse(own_dollar_format(path), f"{module} still formats dollars itself")

    def test_the_output_does_not_change(self):
        self.assertEqual(run("list", os.path.join(ROOT, "stock.txt")).stdout, TABLE)
        self.assertEqual(run("total", os.path.join(ROOT, "stock.txt")).stdout, "$65.80\n")

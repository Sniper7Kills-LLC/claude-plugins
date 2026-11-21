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

from inventory.items import Item
from inventory.report import low_stock


class HiddenLowStockBoundaryTest(unittest.TestCase):
    def test_low_stock_includes_the_threshold(self):
        stock = [Item("X1", "a", 5, 1), Item("X2", "b", 6, 1), Item("X3", "c", 4, 1)]
        self.assertEqual([item.sku for item in low_stock(stock, 5)], ["X1", "X3"])

    def test_cli_lists_the_item_at_the_threshold(self):
        result = run("low", os.path.join(ROOT, "stock.txt"), "--threshold", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["A2", "B7"])

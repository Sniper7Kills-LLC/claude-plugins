import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = os.path.join(ROOT, "stock.txt")


def run(*args):
    return subprocess.run([sys.executable, "-m", "inventory", *args], cwd=ROOT,
                          capture_output=True, text=True)


class CliTest(unittest.TestCase):
    def test_total_prints_the_stock_value(self):
        result = run("total", STOCK)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "$65.80\n")

    def test_list_prints_one_row_per_item(self):
        result = run("list", STOCK)
        self.assertEqual(len(result.stdout.splitlines()), 4)

    def test_low_uses_the_threshold(self):
        result = run("low", STOCK, "--threshold", "4")
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["A2"])

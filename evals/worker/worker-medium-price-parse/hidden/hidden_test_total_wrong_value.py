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

from inventory.items import load


class HiddenPriceParseTest(unittest.TestCase):
    def test_total_of_ten_washers_at_one_fifty(self):
        with tempfile.TemporaryDirectory() as work:
            result = run("total", stock_file(work, "D4,washer,10,1.5\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "$15.00\n")

    def test_prices_with_one_and_two_decimal_digits(self):
        with tempfile.TemporaryDirectory() as work:
            stock = load(stock_file(work, "D4,washer,10,1.5\nD5,pin,1,12.05\n"))
        self.assertEqual([item.price for item in stock], [150, 1205])

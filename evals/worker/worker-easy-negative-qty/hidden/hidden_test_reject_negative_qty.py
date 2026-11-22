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

from inventory.items import Item, parse_line


class HiddenNegativeQuantityTest(unittest.TestCase):
    def test_rejects_a_negative_quantity(self):
        with self.assertRaisesRegex(ValueError, "quantity must not be negative"):
            parse_line("A1,bolt,-2,0.25")

    def test_accepts_a_zero_quantity(self):
        self.assertEqual(parse_line("A1,bolt,0,0.25"), Item("A1", "bolt", 0, 25))

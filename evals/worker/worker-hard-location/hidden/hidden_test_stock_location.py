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

STOCK = "A1,bolt,40,0.25,north\nA2,nut,3,0.10,south\nB7,hinge,2,4.50\nC3,bracket,1,2.75,north\n"


class HiddenLocationTest(unittest.TestCase):
    def test_a_fifth_field_is_the_location(self):
        self.assertEqual(parse_line("A1,bolt,40,0.25,north").location, "north")
        self.assertEqual(parse_line("A1,bolt,40,0.25").location, "main")

    def test_item_keeps_four_positional_fields(self):
        self.assertEqual(Item("A1", "bolt", 40, 25).location, "main")

    def test_list_ends_each_row_with_the_location(self):
        with tempfile.TemporaryDirectory() as work:
            result = run("list", stock_file(work, STOCK))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = result.stdout.splitlines()
        self.assertEqual(rows[0], "A1     bolt            40     $0.25 north")
        self.assertEqual([row.rsplit(" ", 1)[1] for row in rows], ["north", "south", "main", "north"])

    def test_total_for_one_location(self):
        with tempfile.TemporaryDirectory() as work:
            path = stock_file(work, STOCK)
            north = run("total", "--location", "north", path)
            every = run("total", path)
        self.assertEqual(north.stdout, "$12.75\n", north.stderr)
        self.assertEqual(every.stdout, "$22.05\n", every.stderr)

    def test_low_for_one_location(self):
        with tempfile.TemporaryDirectory() as work:
            path = stock_file(work, STOCK)
            north = run("low", "--location", "north", path)
            every = run("low", path)
        self.assertEqual([row.split()[0] for row in north.stdout.splitlines()], ["C3"], north.stderr)
        self.assertEqual([row.split()[0] for row in every.stdout.splitlines()], ["A2", "B7", "C3"], every.stderr)

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

TABLE = (
    "A1     bolt            40     $0.25\n"
    "A2     nut              3     $0.10\n"
    "B7     hinge            5     $4.50\n"
    "C3     bracket         12     $2.75\n"
)


class HiddenCsvFormatTest(unittest.TestCase):
    def test_csv_prints_a_header_and_rows_in_file_order(self):
        result = run("list", "--format", "csv", os.path.join(ROOT, "stock.txt"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "sku,name,qty,price", "A1,bolt,40,0.25", "A2,nut,3,0.10", "B7,hinge,5,4.50",
            "C3,bracket,12,2.75"])

    def test_the_default_table_does_not_change(self):
        result = run("list", os.path.join(ROOT, "stock.txt"))
        self.assertEqual(result.stdout, TABLE)

    def test_an_unknown_format_exits_2(self):
        result = run("list", "--format", "xml", os.path.join(ROOT, "stock.txt"))
        self.assertEqual(result.returncode, 2)

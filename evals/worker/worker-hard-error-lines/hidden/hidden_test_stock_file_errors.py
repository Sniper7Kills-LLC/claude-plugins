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

from inventory import items

BAD = "A1,bolt,40,0.25\n# comment\nA2,nut,3\n"


class HiddenErrorLinesTest(unittest.TestCase):
    def test_load_raises_stock_file_error_with_path_and_line(self):
        self.assertTrue(issubclass(items.StockFileError, ValueError))
        with tempfile.TemporaryDirectory() as work:
            path = stock_file(work, BAD)
            with self.assertRaises(items.StockFileError) as caught:
                items.load(path)
        self.assertEqual((caught.exception.path, caught.exception.line), (path, 3))

    def test_every_command_names_the_line_and_exits_2(self):
        with tempfile.TemporaryDirectory() as work:
            stock_file(work, BAD, "bad.txt")
            for command in ("list", "total", "low"):
                result = run(command, "bad.txt", cwd=work)
                self.assertEqual(result.returncode, 2, command)
                self.assertEqual(result.stderr.strip(), "bad.txt:3: expected 4 fields, got 3", command)

    def test_a_value_that_is_not_a_number_names_the_line(self):
        with tempfile.TemporaryDirectory() as work:
            stock_file(work, "A1,bolt,forty,0.25\nA2,nut,3,cheap\n", "bad.txt")
            result = run("total", "bad.txt", cwd=work)
        self.assertEqual(result.returncode, 2)
        self.assertRegex(result.stderr, r"^bad\.txt:1: ")
        self.assertNotIn("Traceback", result.stderr)

    def test_parse_line_still_raises_value_error(self):
        with self.assertRaises(ValueError):
            items.parse_line("A1,bolt")

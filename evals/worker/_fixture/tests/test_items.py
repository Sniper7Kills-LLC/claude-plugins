import os
import tempfile
import unittest

from inventory.items import Item, load, parse_line, parse_price


class ParseTest(unittest.TestCase):
    def test_parses_a_full_price(self):
        self.assertEqual(parse_price("12.50"), 1250)

    def test_parses_a_whole_dollar_price(self):
        self.assertEqual(parse_price("3"), 300)

    def test_parses_a_line_into_an_item(self):
        self.assertEqual(parse_line("A1, bolt, 40, 0.25\n"), Item("A1", "bolt", 40, 25))

    def test_rejects_a_line_with_three_fields(self):
        with self.assertRaises(ValueError):
            parse_line("A1,bolt,40")


class LoadTest(unittest.TestCase):
    def test_skips_blank_and_comment_lines(self):
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "stock.txt")
            with open(path, "w") as handle:
                handle.write("# header\n\nA1,bolt,40,0.25\nA2,nut,3,0.10\n")
            self.assertEqual([item.sku for item in load(path)], ["A1", "A2"])

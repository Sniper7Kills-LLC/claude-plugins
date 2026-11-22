import unittest

from inventory.items import Item
from inventory.report import format_line, low_stock, total_value

STOCK = [Item("A1", "bolt", 40, 25), Item("A2", "nut", 3, 10), Item("C3", "bracket", 12, 275)]


class ReportTest(unittest.TestCase):
    def test_total_value_sums_quantity_times_price(self):
        self.assertEqual(total_value(STOCK), 40 * 25 + 3 * 10 + 12 * 275)

    def test_low_stock_lists_items_below_the_threshold(self):
        self.assertEqual([item.sku for item in low_stock(STOCK, 5)], ["A2"])

    def test_format_line_shows_the_price_in_dollars(self):
        self.assertEqual(format_line(STOCK[2]), "C3     bracket         12     $2.75")

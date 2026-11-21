import unittest

from shop.models import Discount, Order
from shop.services import pricing
from tests import order

CODES = {"SAVE10": Discount("SAVE10", 10), "FALL5": Discount("FALL5", 5)}


class PricingTest(unittest.TestCase):
    def test_subtotal_adds_each_line(self):
        item = Order.from_dict(order(1, "2026-11-20T10:00:00Z", [("MUG-1", 2, "12.50"), ("NB-4", 1, "4.20")]))
        self.assertEqual(pricing.subtotal(item), 2920)

    def test_a_code_takes_its_percent_of_the_subtotal(self):
        item = Order.from_dict(order(1, "2026-11-20T10:00:00Z", [("MUG-1", 2, "12.50")], ["SAVE10"]))
        self.assertEqual(pricing.discount(item, CODES), 250)
        self.assertEqual(pricing.total(item, CODES), 2250)

    def test_an_unknown_code_gives_no_discount(self):
        item = Order.from_dict(order(1, "2026-11-20T10:00:00Z", [("MUG-1", 1, "12.50")], ["GONE"]))
        self.assertEqual(pricing.total(item, CODES), 1250)

import unittest

from shop.app import App
from shop.services import InvalidInput, NotFound, OutOfStock
from tests import data_dir


class OrderingTest(unittest.TestCase):
    def setUp(self):
        self.directory = data_dir()
        self.app = App(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_placing_an_order_takes_the_stock(self):
        placed = self.app.ordering.place([{"sku": "MUG-1", "qty": 3}], "2026-11-20T10:00:00Z")
        self.assertEqual(placed.id, 1001)
        self.assertEqual(self.app.catalog.get("MUG-1").stock, 7)
        self.assertEqual(App(self.directory.name).ordering.get(1001).lines[0].qty, 3)

    def test_an_order_beyond_the_stock_is_refused(self):
        with self.assertRaises(OutOfStock):
            self.app.ordering.place([{"sku": "MUG-1", "qty": 11}])

    def test_a_bad_line_is_refused(self):
        for lines in ([], [{"sku": "MUG-1"}], [{"sku": "MUG-1", "qty": 0}]):
            with self.assertRaises(InvalidInput, msg=lines):
                self.app.ordering.place(lines)
        with self.assertRaises(NotFound):
            self.app.ordering.place([{"sku": "NOPE", "qty": 1}])

    def test_cancelling_gives_the_stock_back(self):
        self.app.ordering.place([{"sku": "MUG-1", "qty": 3}])
        self.app.ordering.cancel(1001)
        self.assertEqual(self.app.catalog.get("MUG-1").stock, 10)
        self.assertEqual(self.app.ordering.get(1001).status, "cancelled")

    def test_a_discount_code_is_not_case_sensitive(self):
        self.app.ordering.place([{"sku": "MUG-1", "qty": 1}])
        self.app.ordering.add_discount(1001, " save10 ")
        self.app.ordering.add_discount(1001, "SAVE10")
        self.assertEqual(self.app.ordering.get(1001).discounts, ["SAVE10"])

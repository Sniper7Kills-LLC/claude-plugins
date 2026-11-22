import unittest

from shop.app import App
from tests import data_dir


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.directory = data_dir()
        self.app = App(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_lists_the_products_by_sku(self):
        self.assertEqual([p.sku for p in self.app.catalog.list()], ["MUG-1", "NB-4"])

    def test_a_category_matches_in_any_case_or_spacing(self):
        for category in ("Office Supplies", "office-supplies", "OFFICE  supplies"):
            self.assertEqual([p.sku for p in self.app.catalog.list(category)], ["NB-4"], category)

    def test_the_api_lists_products_of_a_category(self):
        status, payload = self.app.handle("GET", "/products", {"category": "kitchen"})
        self.assertEqual((status, [p["sku"] for p in payload]), (200, ["MUG-1"]))
        self.assertEqual(payload[0]["price"], "$12.50")

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from shop.app import App  # noqa: E402

PRODUCTS = [{"sku": "PEN-5", "name": "Pen", "category": "Office", "price": "24.99", "stock": 20},
            {"sku": "NB-4", "name": "Notebook", "category": "Office", "price": "4.20", "stock": 20}]


def quantities(order):
    """Return the quantity of each product, added over every line of that product."""
    totals = {}
    for line in order["lines"]:
        totals[line["sku"]] = totals.get(line["sku"], 0) + line["qty"]
    return totals


class HiddenRepeatedSkuTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.sample = os.path.join(self.work, "sample")
        shutil.copytree(os.path.join(ROOT, "data"), self.sample)
        self.fresh = os.path.join(self.work, "fresh")
        os.makedirs(self.fresh)
        for name, items in (("products", PRODUCTS), ("orders", []), ("discounts", [])):
            with open(os.path.join(self.fresh, f"{name}.json"), "w") as handle:
                json.dump(items, handle)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_order_1004_of_the_sample_data_has_its_full_total(self):
        status, payload = App(self.sample).handle("GET", "/orders/1004")
        self.assertEqual((status, payload["total_cents"], payload["total"]), (200, 8337, "$83.37"))
        self.assertEqual(quantities(payload), {"PEN-5": 3, "NB-4": 2})

    def test_the_command_line_shows_the_full_total(self):
        result = subprocess.run([sys.executable, "-m", "shop", "--data", self.sample, "order", "show", "1004"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("total $83.37", result.stdout)

    def test_a_new_order_with_a_repeated_product_keeps_both_lines(self):
        lines = [{"sku": "PEN-5", "qty": 1}, {"sku": "NB-4", "qty": 1}, {"sku": "PEN-5", "qty": 2}]
        status, placed = App(self.fresh).handle("POST", "/orders", body={"lines": lines,
                                                                         "placed_at": "2026-11-21T12:00:00Z"})
        self.assertEqual((status, placed["total_cents"]), (201, 7917))
        status, read = App(self.fresh).handle("GET", f"/orders/{placed['id']}")
        self.assertEqual((status, read["total_cents"]), (200, 7917))
        self.assertEqual(quantities(read), {"PEN-5": 3, "NB-4": 1})
        self.assertEqual(App(self.fresh).handle("GET", "/products/PEN-5")[1]["stock"], 17)

    def test_the_daily_report_counts_the_full_total(self):
        lines = [{"sku": "PEN-5", "qty": 1}, {"sku": "PEN-5", "qty": 1}]
        App(self.fresh).handle("POST", "/orders", body={"lines": lines, "placed_at": "2026-11-21T12:00:00Z"})
        status, report = App(self.fresh).handle("GET", "/reports/daily", {"date": "2026-11-21"})
        self.assertEqual((status, report["orders"], report["revenue_cents"]), (200, 1, 4998))

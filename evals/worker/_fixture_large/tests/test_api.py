import os
import subprocess
import sys
import unittest

from shop.app import App
from tests import data_dir, order

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cli(directory, *args):
    return subprocess.run([sys.executable, "-m", "shop", "--data", directory, *args], cwd=ROOT,
                          capture_output=True, text=True)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.directory = data_dir([order(1001, "2026-11-20T09:00:00Z", [("MUG-1", 2, "12.50")])])
        self.app = App(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_shows_an_order_with_its_total(self):
        status, payload = self.app.handle("GET", "/orders/1001")
        self.assertEqual(status, 200)
        self.assertEqual((payload["total_cents"], payload["total"]), (2500, "$25.00"))

    def test_places_an_order(self):
        status, payload = self.app.handle("POST", "/orders", body={"lines": [{"sku": "NB-4", "qty": 2}]})
        self.assertEqual((status, payload["id"], payload["total"]), (201, 1002, "$8.40"))

    def test_applies_a_discount_code(self):
        status, payload = self.app.handle("POST", "/orders/1001/discounts", body={"code": "save10"})
        self.assertEqual((status, payload["discounts"], payload["total"]), (200, ["SAVE10"], "$22.50"))

    def test_lists_the_orders_of_a_day(self):
        status, payload = self.app.handle("GET", "/orders", {"date": "2026-11-20"})
        self.assertEqual((status, [item["id"] for item in payload]), (200, [1001]))

    def test_errors_carry_a_status_and_a_code(self):
        self.assertEqual(self.app.handle("GET", "/orders/9")[0], 404)
        self.assertEqual(self.app.handle("POST", "/orders/1001/discounts", body={"code": "NOPE"})[1]["error"],
                         "not_found")
        self.assertEqual(self.app.handle("POST", "/orders", body={"lines": [{"sku": "MUG-1", "qty": 99}]}),
                         (409, {"error": "out_of_stock", "message": "only 10 of MUG-1 in stock"}))
        self.assertEqual(self.app.handle("GET", "/reports/daily")[1]["error"], "invalid_input")
        self.assertEqual(self.app.handle("DELETE", "/orders/1001")[0], 404)

    def test_the_command_line_shows_an_order(self):
        result = cli(self.directory.name, "order", "show", "1001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("total $25.00", result.stdout)

    def test_the_command_line_reports_an_error(self):
        result = cli(self.directory.name, "order", "discount", "1001", "NOPE")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "error: no discount code NOPE\n")

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

PRODUCTS = [{"sku": "MUG-1", "name": "Mug", "category": "Kitchen", "price": "10.00", "stock": 10}]
DISCOUNTS = [{"code": "SAVE10", "percent": 10}, {"code": "STAFF20", "percent": 20}]


class HiddenOneDiscountTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        orders = [{"id": 7, "placed_at": "2026-11-20T10:00:00Z", "status": "placed",
                   "lines": [{"sku": "MUG-1", "qty": 2, "unit_price": "10.00"}], "discounts": []}]
        for name, items in (("products", PRODUCTS), ("orders", orders), ("discounts", DISCOUNTS)):
            with open(os.path.join(self.directory, f"{name}.json"), "w") as handle:
                json.dump(items, handle)
        self.app = App(self.directory)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def add(self, code, app=None):
        return (app or self.app).handle("POST", "/orders/7/discounts", body={"code": code})

    def test_the_first_code_is_applied(self):
        status, payload = self.add("SAVE10")
        self.assertEqual((status, payload["discounts"], payload["total_cents"]), (200, ["SAVE10"], 1800))

    def test_a_second_code_is_refused_with_409(self):
        self.add("SAVE10")
        status, payload = self.add("STAFF20")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "discount_already_applied")
        self.assertIn("SAVE10", payload["message"])

    def test_the_refused_code_changes_nothing_also_after_a_new_read(self):
        self.add("SAVE10")
        self.add("staff20")
        for app in (self.app, App(self.directory)):
            status, payload = app.handle("GET", "/orders/7")
            self.assertEqual((status, payload["discounts"], payload["total_cents"]), (200, ["SAVE10"], 1800))

    def test_the_same_code_again_changes_nothing(self):
        self.add("SAVE10")
        status, payload = self.add(" save10 ")
        self.assertEqual((status, payload["discounts"], payload["total_cents"]), (200, ["SAVE10"], 1800))

    def test_the_command_line_refuses_a_second_code(self):
        self.add("SAVE10")
        result = subprocess.run([sys.executable, "-m", "shop", "--data", self.directory,
                                 "order", "discount", "7", "STAFF20"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertTrue(lines[0].startswith("error:"), result.stderr)
        self.assertIn("SAVE10", lines[0])

    def test_the_api_document_lists_the_new_error(self):
        with open(os.path.join(ROOT, "docs", "api.md")) as handle:
            self.assertIn("discount_already_applied", handle.read())

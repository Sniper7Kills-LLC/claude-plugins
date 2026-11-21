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


def write_data(directory, orders):
    for name, items in (("products", PRODUCTS), ("orders", orders), ("discounts", [])):
        with open(os.path.join(directory, f"{name}.json"), "w") as handle:
            json.dump(items, handle)


def order(order_id, placed_at):
    return {"id": order_id, "placed_at": placed_at, "status": "placed",
            "lines": [{"sku": "MUG-1", "qty": 1, "unit_price": "10.00"}], "discounts": []}


class HiddenReportMidnightTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        write_data(self.directory, [order(1, "2026-11-20T00:00:00Z"), order(2, "2026-11-20T23:59:59Z"),
                                    order(3, "2026-11-21T00:00:00Z"), order(4, "2026-11-21T12:00:00Z")])
        self.app = App(self.directory)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_an_order_at_midnight_counts_only_in_the_day_it_starts(self):
        status, first = self.app.handle("GET", "/reports/daily", {"date": "2026-11-20"})
        self.assertEqual((status, first["orders"], first["revenue_cents"]), (200, 2, 2000))
        status, second = self.app.handle("GET", "/reports/daily", {"date": "2026-11-21"})
        self.assertEqual((status, second["orders"], second["revenue_cents"]), (200, 2, 2000))

    def test_the_orders_of_a_day_use_the_same_rule(self):
        for day, expected in (("2026-11-20", [1, 2]), ("2026-11-21", [3, 4]), ("2026-11-19", [])):
            status, payload = self.app.handle("GET", "/orders", {"date": day})
            self.assertEqual((status, [item["id"] for item in payload]), (200, expected), day)

    def test_the_command_line_report_of_the_sample_data(self):
        with tempfile.TemporaryDirectory() as work:
            data = os.path.join(work, "data")
            shutil.copytree(os.path.join(ROOT, "data"), data)
            result = subprocess.run([sys.executable, "-m", "shop", "--data", data, "report", "daily", "2026-11-20"],
                                    cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "2026-11-20: 2 orders, revenue $66.17\n")

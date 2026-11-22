import datetime
import unittest

from shop.app import App
from tests import data_dir, order


class DailyReportTest(unittest.TestCase):
    def test_counts_the_placed_orders_of_the_day(self):
        orders = [order(1001, "2026-11-20T09:00:00Z", [("MUG-1", 2, "12.50")]),
                  order(1002, "2026-11-20T18:00:00Z", [("NB-4", 1, "4.20")], ["SAVE10"]),
                  order(1003, "2026-11-20T19:00:00Z", [("NB-4", 3, "4.20")], status="cancelled"),
                  order(1004, "2026-11-22T08:00:00Z", [("MUG-1", 1, "12.50")])]
        with data_dir(orders) as directory:
            report = App(directory).reports.daily(datetime.date(2026, 11, 20))
        self.assertEqual(report, {"date": "2026-11-20", "orders": 2, "revenue_cents": 2878,
                                  "revenue": "$28.78"})

    def test_a_day_without_orders(self):
        with data_dir() as directory:
            report = App(directory).reports.daily(datetime.date(2026, 11, 20))
        self.assertEqual((report["orders"], report["revenue"]), (0, "$0.00"))

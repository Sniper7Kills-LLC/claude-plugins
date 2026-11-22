import datetime
import unittest

from shop.util import dates


class DatesTest(unittest.TestCase):
    def test_parses_a_utc_time_with_a_z_suffix(self):
        value = dates.parse_timestamp("2026-11-20T09:15:00Z")
        self.assertEqual(value, datetime.datetime(2026, 11, 20, 9, 15, tzinfo=dates.UTC))

    def test_converts_an_offset_to_utc(self):
        value = dates.parse_timestamp("2026-11-20T23:30:00-02:00")
        self.assertEqual(dates.format_timestamp(value), "2026-11-21T01:30:00Z")

    def test_a_day_holds_its_morning_and_its_last_second(self):
        day = datetime.date(2026, 11, 20)
        self.assertTrue(dates.in_day(dates.parse_timestamp("2026-11-20T00:00:00Z"), day))
        self.assertTrue(dates.in_day(dates.parse_timestamp("2026-11-20T23:59:59Z"), day))
        self.assertFalse(dates.in_day(dates.parse_timestamp("2026-11-19T23:59:59Z"), day))

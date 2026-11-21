import unittest

from shop.util.money import format_money, parse_money, percent_of


class MoneyTest(unittest.TestCase):
    def test_parses_dollars_and_cents(self):
        self.assertEqual(parse_money("12.50"), 1250)
        self.assertEqual(parse_money("3"), 300)
        self.assertEqual(parse_money("$1.5"), 150)

    def test_refuses_text_that_is_not_an_amount(self):
        for text in ("", "abc", "1.234", "-2"):
            with self.assertRaises(ValueError, msg=text):
                parse_money(text)

    def test_formats_cents(self):
        self.assertEqual(format_money(1250), "$12.50")
        self.assertEqual(format_money(5), "$0.05")
        self.assertEqual(format_money(-120), "-$1.20")

    def test_rounds_a_percent_half_up(self):
        self.assertEqual(percent_of(1250, 10), 125)
        self.assertEqual(percent_of(4575, 10), 458)

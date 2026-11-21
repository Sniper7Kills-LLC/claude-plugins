import os
import tempfile
import unittest

import widgets


class CountTest(unittest.TestCase):
    def test_counts_non_empty_lines(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("a\n\nb\n")
        try:
            self.assertEqual(widgets.count(handle.name), 2)
        finally:
            os.unlink(handle.name)


if __name__ == "__main__":
    unittest.main()

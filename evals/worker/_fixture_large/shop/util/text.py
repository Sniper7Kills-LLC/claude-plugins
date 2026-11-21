"""Text helpers for names, categories and codes."""

import re


def slugify(text):
    """Return lowercase words joined by ``-``: ``"Office Supplies"`` gives ``office-supplies``."""
    return "-".join(re.findall(r"[a-z0-9]+", str(text).lower()))


def normalize_code(code):
    """Return a discount code in its stored form: no outer spaces, upper case."""
    return str(code).strip().upper()

"""Settings that the command line and the tests share."""

import os

DEFAULT_DATA_DIR = "data"
# The first id of a new data directory; later orders count up from the highest id.
FIRST_ORDER_ID = 1001
CURRENCY_SYMBOL = "$"


def data_dir(override=None):
    """Return the data directory: the override, then $SHOP_DATA, then data/."""
    return override or os.environ.get("SHOP_DATA") or DEFAULT_DATA_DIR

"""The rules of the shop. A service raises a ShopError; the API turns it into a response."""


class ShopError(Exception):
    """A request that the shop refuses."""


class NotFound(ShopError):
    """No item has the key."""


class InvalidInput(ShopError):
    """A field is missing or has a bad value."""


class OutOfStock(ShopError):
    """The stock is too low for the order."""

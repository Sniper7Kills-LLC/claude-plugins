"""Error responses. docs/api.md lists each status and code."""

from shop.services import InvalidInput, NotFound, OutOfStock

# The first matching class wins, so a subclass goes before its parent.
STATUS = [
    (OutOfStock, 409, "out_of_stock"),
    (NotFound, 404, "not_found"),
    (InvalidInput, 400, "invalid_input"),
]


def error_response(error):
    """Return (status, payload) for a ShopError."""
    for kind, status, code in STATUS:
        if isinstance(error, kind):
            return status, {"error": code, "message": str(error)}
    return 400, {"error": "invalid_input", "message": str(error)}


def no_route(method, path):
    return 404, {"error": "not_found", "message": f"no route {method} {path}"}

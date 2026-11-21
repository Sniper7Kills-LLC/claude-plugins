# The request API

`App(data_dir).handle(method, path, query=None, body=None)` returns `(status, payload)`.
`payload` is a dict or a list. The command line uses the same call.

| Method | Path | Query or body | Result |
|---|---|---|---|
| GET | `/products` | `category` (optional) | 200, a list of products |
| GET | `/products/<sku>` | | 200, one product |
| GET | `/orders` | `date` (optional, `YYYY-MM-DD`) | 200, a list of orders placed on that UTC day |
| GET | `/orders/<id>` | | 200, one order |
| POST | `/orders` | `{"lines": [{"sku", "qty"}], "placed_at"}` | 201, the new order |
| POST | `/orders/<id>/discounts` | `{"code"}` | 200, the order with the code applied |
| POST | `/orders/<id>/cancel` | | 200, the cancelled order |
| GET | `/reports/daily` | `date` (`YYYY-MM-DD`) | 200, `{date, orders, revenue_cents, revenue}` |

An order is `{id, placed_at, status, lines, discounts, subtotal_cents, discount_cents,
total_cents, total}`. `total` is the formatted amount, for example `"$12.50"`.

## Errors

An error is `{"error": <code>, "message": <text>}`.

| Status | `error` | When |
|---|---|---|
| 400 | `invalid_input` | A field is missing or has a bad value. |
| 404 | `not_found` | No product, order, discount code or route matches. |
| 409 | `out_of_stock` | The stock is too low for the order. |

A discount code is not case-sensitive. Adding a code that the order already has changes
nothing.

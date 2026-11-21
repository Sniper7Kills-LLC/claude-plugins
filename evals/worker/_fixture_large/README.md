# shop

A small order service for a shop. It keeps products, orders and discount codes in JSON
files, and gives them through a request API and a command line.

```bash
python3 -m shop products
python3 -m shop orders --date 2026-11-20
python3 -m shop order show 1001
python3 -m shop order discount 1002 SAVE10
python3 -m shop report daily 2026-11-20
```

`--data <dir>` gives the data directory. The default is `$SHOP_DATA`, or else `data/`.

## Layout

| Path | Content |
|---|---|
| `shop/app.py` | `App`: connects the store, the services and the API router. |
| `shop/api/` | The request API: the router, the handlers and the error responses. |
| `shop/services/` | The rules: catalog, ordering, discounts, pricing and reports. |
| `shop/models/` | The data types: `Product`, `Order`, `Line` and `Discount`. |
| `shop/storage/` | The JSON store and the repositories. |
| `shop/util/` | Money, date and text helpers. |
| `shop/cli.py` | The command line. It sends each command through `App.handle`. |
| `data/` | The sample data. |
| `docs/api.md` | The request API. |

## Data

- `products.json`: a list of `{sku, name, category, price, stock}`. `price` is in dollars,
  for example `"12.50"`.
- `orders.json`: a list of `{id, placed_at, status, lines, discounts}`. `placed_at` is an
  ISO 8601 time in UTC. Each line is `{sku, qty, unit_price}`. `status` is `placed` or
  `cancelled`.
- `discounts.json`: a list of `{code, percent}`.

All amounts are whole cents inside the service.

Python 3, standard library only.

Test command: `python3 -m unittest`

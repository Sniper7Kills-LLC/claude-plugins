# inventory

A small command-line tool that lists, counts and values the items in a stock file.

```bash
python3 -m inventory list stock.txt
python3 -m inventory total stock.txt
python3 -m inventory low stock.txt --threshold 5
```

A stock file has one item per line: `sku,name,qty,price`. The price is in dollars, for
example `12.50`. Blank lines and lines that start with `#` are skipped.

Python 3, standard library only.

Test command: `python3 -m unittest`

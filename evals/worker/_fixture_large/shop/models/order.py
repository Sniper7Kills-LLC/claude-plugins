from dataclasses import dataclass, field

from shop.util.dates import format_timestamp, parse_timestamp
from shop.util.money import format_money, parse_money

PLACED = "placed"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class Line:
    sku: str
    qty: int
    unit_cents: int

    @property
    def amount_cents(self):
        return self.qty * self.unit_cents


@dataclass
class Order:
    id: int
    placed_at: object  # an aware datetime in UTC
    status: str = PLACED
    lines: list = field(default_factory=list)
    discounts: list = field(default_factory=list)  # normalized codes, in the order applied

    @classmethod
    def from_dict(cls, data):
        lines = [Line(item["sku"], int(item["qty"]), parse_money(item["unit_price"]))
                 for item in data["lines"]]
        return cls(id=int(data["id"]), placed_at=parse_timestamp(data["placed_at"]),
                   status=data.get("status", PLACED), lines=merge_lines(lines),
                   discounts=list(data.get("discounts", [])))

    def to_dict(self):
        """Return the stored form."""
        return {"id": self.id, "placed_at": format_timestamp(self.placed_at), "status": self.status,
                "lines": [{"sku": line.sku, "qty": line.qty,
                           "unit_price": format_money(line.unit_cents).lstrip("$")}
                          for line in self.lines],
                "discounts": list(self.discounts)}


def merge_lines(lines):
    """Return one line per product, in the order the products first appear."""
    merged = {}
    for line in lines:
        merged[line.sku] = line
    return list(merged.values())

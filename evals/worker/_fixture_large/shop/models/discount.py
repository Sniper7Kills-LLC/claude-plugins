from dataclasses import dataclass

from shop.util.text import normalize_code


@dataclass(frozen=True)
class Discount:
    code: str
    percent: int

    @classmethod
    def from_dict(cls, data):
        return cls(code=normalize_code(data["code"]), percent=int(data["percent"]))

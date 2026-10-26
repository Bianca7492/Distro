"""Exact money arithmetic.

Money is integer minor units plus a currency code. Never float. Rounding
decisions are explicit, because reconciliation must match a distributor's own
totals to the cent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

# Minor-unit exponent per currency. Extend as markets are added.
EXPONENT = {
    "USD": 2, "EUR": 2, "GBP": 2, "NGN": 2, "INR": 2, "KES": 2,
    "ZAR": 2, "BRL": 2, "MXN": 2, "JPY": 0, "KRW": 0,
}


class CurrencyError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Money:
    """An exact monetary amount in minor units."""

    minor: int
    currency: str

    def __post_init__(self) -> None:
        cur = self.currency.upper()
        if cur not in EXPONENT:
            raise CurrencyError(f"unknown currency: {self.currency!r}")
        object.__setattr__(self, "currency", cur)
        if not isinstance(self.minor, int):
            raise TypeError("minor must be an int")

    @property
    def exponent(self) -> int:
        return EXPONENT[self.currency]

    @property
    def scale(self) -> int:
        return 10 ** self.exponent

    @classmethod
    def from_decimal(cls, amount: Decimal | str, currency: str) -> "Money":
        d = Decimal(str(amount))
        scale = 10 ** EXPONENT[currency.upper()]
        minor = int((d * scale).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        return cls(minor, currency)

    def to_decimal(self) -> Decimal:
        return Decimal(self.minor) / self.scale

    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyError(
                f"currency mismatch: {self.currency} vs {other.currency}"
            )

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor - other.minor, self.currency)

    def __mul__(self, factor: int) -> "Money":
        if not isinstance(factor, int):
            raise TypeError("Money multiplies by int only; use apply_rate for %")
        return Money(self.minor * factor, self.currency)

    def apply_rate(self, rate: Decimal | str) -> "Money":
        """Apply a rate (e.g. a 0.70 share) with banker's rounding."""
        r = Decimal(str(rate))
        minor = int(
            (Decimal(self.minor) * r).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        )
        return Money(minor, self.currency)

    def __str__(self) -> str:
        return f"{self.to_decimal():.{self.exponent}f} {self.currency}"

    def __repr__(self) -> str:
        return f"Money({self.minor}, {self.currency!r})"

    def zero(self) -> "Money":
        return Money(0, self.currency)


def split_exact(total: Money, weights: list[int]) -> list[Money]:
    """Split ``total`` by integer weights with no cent lost or created.

    Largest-remainder method: the sum of the parts always equals ``total``.
    This is the allocation primitive for royalty splits.
    """
    if not weights:
        return []
    if any(w < 0 for w in weights):
        raise ValueError("weights must be non-negative")
    denom = sum(weights)
    if denom == 0:
        raise ValueError("weights must not sum to zero")

    base = []
    remainders = []
    allocated = 0
    for w in weights:
        num = total.minor * w
        q, r = divmod(num, denom)
        base.append(q)
        remainders.append((r, len(base) - 1))
        allocated += q

    remainder = total.minor - allocated
    remainders.sort(key=lambda t: (-t[0], t[1]))
    for i in range(remainder):
        base[remainders[i % len(remainders)][1]] += 1

    return [Money(b, total.currency) for b in base]

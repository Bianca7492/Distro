"""Royalty splits (S2-10).

Splits are declared in basis points: 10000 bps = 100%. Integer bps, not
percentages, so a declaration either sums exactly or is rejected. A percentage
float would let 33.33 x 3 pass as "about 100%" and then fail to distribute.

The rule: splits must sum to exactly 10000 before a track is deliverable. An
ERN with splits that do not sum to the whole is rejected by the DSP, and a
silent rounding difference means someone is paid the wrong amount.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TOTAL_BPS = 10000

# Roles recognised in a split declaration. Kept deliberately small: each one
# maps to a DDEX contributor role in the ERN builder.
ROLES = (
    "songwriter",
    "composer",
    "lyricist",
    "producer",
    "performer",
    "publisher",
    "label",
    "administrator",
)


class SplitError(ValueError):
    """A split declaration is unusable. Never silently repaired."""


@dataclass(frozen=True)
class Split:
    party: str
    role: str
    share_bps: int

    def __post_init__(self) -> None:
        if not self.party or not self.party.strip():
            raise SplitError("split party is required")
        if self.role not in ROLES:
            raise SplitError(
                f"unknown split role {self.role!r}; expected one of {ROLES}"
            )
        if not isinstance(self.share_bps, int) or isinstance(
            self.share_bps, bool
        ):
            raise SplitError(
                f"share_bps must be an integer, got {self.share_bps!r}"
            )
        if self.share_bps < 0:
            raise SplitError(f"share_bps cannot be negative: {self.share_bps}")
        if self.share_bps > TOTAL_BPS:
            raise SplitError(
                f"share_bps cannot exceed {TOTAL_BPS}: {self.share_bps}"
            )


@dataclass
class SplitSheet:
    """A validated set of splits for one track or work."""

    splits: list[Split] = field(default_factory=list)

    @property
    def total_bps(self) -> int:
        return sum(s.share_bps for s in self.splits)

    @property
    def is_complete(self) -> bool:
        return self.total_bps == TOTAL_BPS

    def validate_for_delivery(self) -> None:
        """Raise unless the splits sum to exactly 10000 bps.

        Delivery is blocked rather than proceeding with an incomplete sheet:
        an ERN whose splits do not sum to the whole is rejected by the DSP, and
        guessing the remainder would pay the wrong party.
        """
        if not self.splits:
            raise SplitError("no splits declared")
        total = self.total_bps
        if total != TOTAL_BPS:
            raise SplitError(
                f"splits sum to {total} bps, not {TOTAL_BPS} "
                f"({total / 100:.2f}% of 100%)"
            )

    def distribute(self, amount_minor: int) -> list[tuple[str, int]]:
        """Apportion an amount of money across the splits, exactly.

        Returns (party, amount_minor) pairs whose amounts sum to the input.
        Uses largest-remainder so no cent is lost or invented: integer division
        alone would drop the remainder, and rounding each share independently
        would not sum back to the total.
        """
        self.validate_for_delivery()
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
            raise SplitError(f"amount must be an integer, got {amount_minor!r}")

        sign = -1 if amount_minor < 0 else 1
        amount = abs(amount_minor)

        # Floor each share, then hand the remainder to the largest fractions.
        shares = []
        for s in self.splits:
            exact = amount * s.share_bps
            shares.append([s.party, exact // TOTAL_BPS, exact % TOTAL_BPS])

        remainder = amount - sum(sh[1] for sh in shares)
        # Ties broken by declaration order, so the result is deterministic.
        order = sorted(
            range(len(shares)), key=lambda i: (-shares[i][2], i)
        )
        for i in order[:remainder]:
            shares[i][1] += 1

        return [(sh[0], sign * sh[1]) for sh in shares]


def parse_splits(raw: list[dict]) -> SplitSheet:
    """Build a SplitSheet from the JSON shape stored in the database.

    The stored shape is ``[{party, role, share_bps}]``. Unknown keys are
    ignored; a missing or malformed one raises.
    """
    if not isinstance(raw, list):
        raise SplitError(f"splits must be a list, got {type(raw).__name__}")
    splits = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SplitError(f"split {i} must be an object, got {item!r}")
        try:
            splits.append(
                Split(
                    party=item["party"],
                    role=item["role"],
                    share_bps=item["share_bps"],
                )
            )
        except KeyError as exc:
            raise SplitError(f"split {i} is missing {exc.args[0]!r}") from exc
    return SplitSheet(splits)

"""Stability index (S2-18).

A variance measure over engagement. It **does not create revenue**. It sets the
discount rate applied to revenue that already exists: stable engagement -> a
lower discount rate -> a higher valuation per unit of *proven* revenue. Nothing
here mints anything, and engagement is never treated as money.

Definition
----------
Over a window of daily play counts:

    cv       = stdev(daily plays) / mean(daily plays)    coefficient of variation
    score    = 1 / (1 + cv)                              in (0, 1], 1 = perfectly steady
    discount = MAX_DISCOUNT_BPS * cv / CV_CEILING        in bps, saturating

`score` is what a reader understands: 1.0 is perfectly steady. `discount` is
what the valuation uses. The discount is linear in cv and saturates at the cap,
so a maximally unstable track actually reaches the cap. Deriving the discount
from the rounded score instead would leave half the range unreachable and make
the two stored numbers disagree by a hair.

Edge cases, decided explicitly rather than left to arithmetic:
  * fewer than MIN_OBSERVATIONS days -> no index. Too little signal.
  * mean of zero                     -> cv undefined; treated as maximally
                                        unstable (cv = CV_CEILING). A track with
                                        no plays has demonstrated no stability.
  * single observation               -> no index; stdev is undefined.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Below this many days, the variance estimate is noise.
MIN_OBSERVATIONS = 7

# The largest discount the stability channel may apply. Stability reduces the
# discount rate; it never drives the rate to zero, because stability is not the
# only risk factor.
MAX_DISCOUNT_BPS = 1000   # 10%

# cv is capped here so a single outlier cannot drive the discount to the cap
# for a track that is otherwise steady, and so `score` has a floor of 0.25
# rather than approaching zero.
CV_CEILING = 3.0


class StabilityError(ValueError):
    """The input cannot produce a stability index."""


@dataclass(frozen=True)
class StabilityResult:
    cv: float
    score: float
    discount_bps: int
    observations: int
    mean_plays: float
    stdev_plays: float

    @property
    def cv_bps(self) -> int:
        return round(self.cv * 10000)


def coefficient_of_variation(values: list[int | float]) -> float:
    """Sample coefficient of variation, capped at CV_CEILING.

    Population stdev, not sample: the window is the whole observation period,
    not a sample drawn from a larger one.
    """
    n = len(values)
    if n < 2:
        raise StabilityError("need at least two observations")

    mean = sum(values) / n
    if mean <= 0:
        # No plays: no demonstrated stability. Maximum instability.
        return CV_CEILING

    variance = sum((v - mean) ** 2 for v in values) / n
    cv = math.sqrt(variance) / mean
    return min(cv, CV_CEILING)


def compute(daily_plays: list[int | float]) -> StabilityResult:
    """Compute the stability index for a window of daily play counts.

    Raises StabilityError when there is not enough signal to say anything.
    """
    if not daily_plays:
        raise StabilityError("no observations")
    if len(daily_plays) < MIN_OBSERVATIONS:
        raise StabilityError(
            f"need at least {MIN_OBSERVATIONS} observations, got "
            f"{len(daily_plays)}"
        )
    if any(p < 0 for p in daily_plays):
        raise StabilityError("daily play counts cannot be negative")

    n = len(daily_plays)
    mean = sum(daily_plays) / n
    cv = coefficient_of_variation(daily_plays)

    score = 1.0 / (1.0 + cv)
    discount_bps = min(
        MAX_DISCOUNT_BPS, round(MAX_DISCOUNT_BPS * cv / CV_CEILING)
    )

    if mean <= 0:
        stdev = 0.0
    else:
        stdev = math.sqrt(sum((v - mean) ** 2 for v in daily_plays) / n)

    return StabilityResult(
        cv=cv,
        score=round(score, 4),
        discount_bps=discount_bps,
        observations=n,
        mean_plays=mean,
        stdev_plays=stdev,
    )

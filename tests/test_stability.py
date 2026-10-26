"""Stability index (S2-18).

The point of these tests is the boundary between "a number" and "a defensible
number". A stability score feeds a discount rate on real revenue, so a track
with four days of history must produce no index rather than a confident-looking
figure built from noise.
"""

from __future__ import annotations

import pytest

from catalog.stability import (
    CV_CEILING,
    MAX_DISCOUNT_BPS,
    MIN_OBSERVATIONS,
    StabilityError,
    coefficient_of_variation,
    compute,
)


# ---------------------------------------------------------------------------
# Insufficient signal
# ---------------------------------------------------------------------------

def test_no_observations_is_rejected():
    with pytest.raises(StabilityError):
        compute([])


def test_below_minimum_observations_is_rejected():
    with pytest.raises(StabilityError) as exc:
        compute([10] * (MIN_OBSERVATIONS - 1))
    assert str(MIN_OBSERVATIONS) in str(exc.value)


def test_exactly_minimum_observations_is_accepted():
    result = compute([10] * MIN_OBSERVATIONS)
    assert result.observations == MIN_OBSERVATIONS


def test_negative_plays_are_rejected():
    with pytest.raises(StabilityError):
        compute([10, 10, 10, -1, 10, 10, 10])


# ---------------------------------------------------------------------------
# Perfect stability
# ---------------------------------------------------------------------------

def test_flat_engagement_scores_one_and_takes_no_discount():
    result = compute([100] * 30)
    assert result.cv == 0.0
    assert result.score == 1.0
    assert result.discount_bps == 0


def test_flat_engagement_at_any_level_is_equally_stable():
    """Stability is about variance, not magnitude. A track with 10 plays a day
    every day is as stable as one with 10,000."""
    low = compute([10] * 30)
    high = compute([10_000] * 30)
    assert low.score == high.score == 1.0
    assert low.discount_bps == high.discount_bps == 0


# ---------------------------------------------------------------------------
# Instability
# ---------------------------------------------------------------------------

def test_volatile_engagement_scores_lower_and_discounts_more():
    steady = compute([100] * 30)
    volatile = compute([10, 500, 20, 900, 5, 700, 30, 400, 15, 800] * 3)
    assert volatile.score < steady.score
    assert volatile.discount_bps > steady.discount_bps


def test_score_and_discount_move_in_opposite_directions():
    """A steadier track must never take a larger discount than a less steady
    one. The discount is linear in cv and saturates at the cap."""
    previous = None
    for plays in ([100] * 20, [10, 90] * 10, [1, 999] * 10, [0, 100000] * 10):
        r = compute(plays)
        expected = min(
            MAX_DISCOUNT_BPS, round(MAX_DISCOUNT_BPS * r.cv / CV_CEILING)
        )
        assert r.discount_bps == expected
        if previous is not None:
            assert r.discount_bps >= previous
        previous = r.discount_bps


def test_discount_never_exceeds_the_cap():
    r = compute([0, 100000] * 20)
    assert r.discount_bps <= MAX_DISCOUNT_BPS


def test_cv_is_capped_so_one_outlier_does_not_wipe_the_score():
    """A single spike should not drive the score to zero. Otherwise a track
    with one viral day looks like a track in freefall."""
    r = compute([100] * 29 + [1_000_000])
    assert r.cv <= CV_CEILING
    assert r.score > 0


# ---------------------------------------------------------------------------
# Degenerate windows
# ---------------------------------------------------------------------------

def test_no_plays_at_all_is_maximum_instability():
    """A track with no plays has demonstrated nothing. It must not score as
    perfectly stable, which a naive mean-of-zero would produce."""
    r = compute([0] * 30)
    assert r.cv == CV_CEILING
    assert r.score < 1.0
    assert r.discount_bps > 0


def test_a_single_nonzero_day_among_zeros_is_unstable():
    r = compute([0] * 29 + [5000])
    assert r.score < 0.5


# ---------------------------------------------------------------------------
# The statistic itself
# ---------------------------------------------------------------------------

def test_coefficient_of_variation_matches_the_textbook_value():
    """Population stdev, not sample: the window is the whole observation
    period, so the divisor is n. [40, 50, 60] has mean 50 and population
    stdev sqrt(200/3) ~= 8.165, giving cv ~= 0.1633 — not the 0.2 that the
    sample stdev (divisor n-1) would produce."""
    cv = coefficient_of_variation([40, 50, 60])
    assert cv == pytest.approx((200 / 3) ** 0.5 / 50, abs=1e-12)

    # mean 40, population stdev sqrt(800/3) ~= 16.33 -> cv ~= 0.40825
    cv = coefficient_of_variation([20, 40, 60])
    assert cv == pytest.approx((800 / 3) ** 0.5 / 40, abs=1e-12)


def test_coefficient_of_variation_needs_two_points():
    with pytest.raises(StabilityError):
        coefficient_of_variation([5])


def test_score_is_always_within_the_unit_interval():
    for plays in (
        [0] * 10,
        [1] * 10,
        [1, 2, 3, 4, 5, 6, 7],
        [0, 1000000] * 5,
    ):
        r = compute(plays)
        assert 0.0 < r.score <= 1.0
        assert 0 <= r.discount_bps <= MAX_DISCOUNT_BPS


def test_result_reports_the_underlying_statistics():
    r = compute([10, 20, 30, 40, 50, 60, 70])
    assert r.observations == 7
    assert r.mean_plays == pytest.approx(40.0)
    assert r.stdev_plays > 0
    assert r.cv_bps == round(r.cv * 10000)


def test_computation_is_deterministic():
    plays = [3, 7, 1, 9, 4, 8, 2, 6, 5, 10]
    assert compute(plays) == compute(plays)

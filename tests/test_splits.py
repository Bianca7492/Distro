"""Royalty splits (S2-10)."""

from __future__ import annotations

import pytest

from catalog.splits import (
    TOTAL_BPS,
    Split,
    SplitError,
    SplitSheet,
    parse_splits,
)


def sheet(*triples) -> SplitSheet:
    return SplitSheet([Split(*t) for t in triples])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_splits_summing_to_100_percent_are_valid():
    s = sheet(("Alice", "songwriter", 6000), ("Bob", "producer", 4000))
    s.validate_for_delivery()
    assert s.total_bps == TOTAL_BPS
    assert s.is_complete


def test_splits_that_do_not_sum_to_100_percent_are_rejected():
    s = sheet(("Alice", "songwriter", 6000), ("Bob", "producer", 3000))
    assert not s.is_complete
    with pytest.raises(SplitError) as exc:
        s.validate_for_delivery()
    assert "9000" in str(exc.value)


def test_splits_summing_over_100_percent_are_rejected():
    s = sheet(("Alice", "songwriter", 6000), ("Bob", "producer", 5000))
    with pytest.raises(SplitError):
        s.validate_for_delivery()


def test_empty_splits_are_rejected():
    with pytest.raises(SplitError):
        SplitSheet().validate_for_delivery()


def test_single_party_can_hold_everything():
    s = sheet(("Alice", "songwriter", TOTAL_BPS))
    s.validate_for_delivery()
    assert s.distribute(10000) == [("Alice", 10000)]


def test_three_way_split_does_not_lose_a_cent():
    """33.33% three ways is the classic rounding trap."""
    s = sheet(
        ("A", "songwriter", 3333),
        ("B", "songwriter", 3333),
        ("C", "songwriter", 3334),
    )
    s.validate_for_delivery()
    parts = s.distribute(10000)
    assert sum(p[1] for p in parts) == 10000


# ---------------------------------------------------------------------------
# Split value validation
# ---------------------------------------------------------------------------

def test_share_must_be_an_integer():
    with pytest.raises(SplitError):
        Split("A", "songwriter", 33.33)


def test_booleans_are_not_accepted_as_shares():
    with pytest.raises(SplitError):
        Split("A", "songwriter", True)


def test_negative_share_is_rejected():
    with pytest.raises(SplitError):
        Split("A", "songwriter", -1)


def test_share_over_100_percent_is_rejected():
    with pytest.raises(SplitError):
        Split("A", "songwriter", TOTAL_BPS + 1)


def test_unknown_role_is_rejected():
    with pytest.raises(SplitError) as exc:
        Split("A", "wizard", 5000)
    assert "wizard" in str(exc.value)


def test_empty_party_is_rejected():
    with pytest.raises(SplitError):
        Split("  ", "songwriter", 5000)


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------

def test_distribution_is_exact_and_deterministic():
    s = sheet(
        ("A", "songwriter", 3333),
        ("B", "producer", 3333),
        ("C", "label", 3334),
    )
    first = s.distribute(1_000_001)
    second = s.distribute(1_000_001)
    assert first == second
    assert sum(p[1] for p in first) == 1_000_001


def test_distribution_remainder_goes_to_the_largest_share():
    s = sheet(("A", "songwriter", 5000), ("B", "producer", 5000))
    parts = dict(s.distribute(3))
    assert sum(parts.values()) == 3
    # One party gets 2, the other 1; the split is even so order decides.
    assert sorted(parts.values()) == [1, 2]


def test_distribution_of_zero_is_zero_for_everyone():
    s = sheet(("A", "songwriter", 5000), ("B", "producer", 5000))
    parts = s.distribute(0)
    assert all(p[1] == 0 for p in parts)


def test_distribution_handles_negative_amounts():
    """Refunds and reversals are real; they must not lose cents either."""
    s = sheet(("A", "songwriter", 3333), ("B", "producer", 6667))
    parts = s.distribute(-100)
    assert sum(p[1] for p in parts) == -100


def test_distribution_refuses_an_incomplete_sheet():
    s = sheet(("A", "songwriter", 5000))
    with pytest.raises(SplitError):
        s.distribute(100)


def test_distribution_refuses_a_non_integer_amount():
    s = sheet(("A", "songwriter", TOTAL_BPS))
    with pytest.raises(SplitError):
        s.distribute(10.5)


@pytest.mark.parametrize("amount", [1, 7, 99, 12345, 999_999, 10**9 + 1])
def test_distribution_never_loses_a_minor_unit(amount):
    s = sheet(
        ("A", "songwriter", 1),
        ("B", "producer", 3333),
        ("C", "label", 3333),
        ("D", "publisher", 3333),
    )
    assert sum(p[1] for p in s.distribute(amount)) == amount


# ---------------------------------------------------------------------------
# Parsing from stored JSON
# ---------------------------------------------------------------------------

def test_parse_splits_from_stored_shape():
    s = parse_splits(
        [
            {"party": "A", "role": "songwriter", "share_bps": 5000},
            {"party": "B", "role": "producer", "share_bps": 5000},
        ]
    )
    s.validate_for_delivery()
    assert len(s.splits) == 2


def test_parse_splits_rejects_a_missing_key():
    with pytest.raises(SplitError) as exc:
        parse_splits([{"party": "A", "role": "songwriter"}])
    assert "share_bps" in str(exc.value)


def test_parse_splits_rejects_a_non_list():
    with pytest.raises(SplitError):
        parse_splits({"party": "A"})


def test_parse_splits_rejects_a_non_object_entry():
    with pytest.raises(SplitError):
        parse_splits(["A"])

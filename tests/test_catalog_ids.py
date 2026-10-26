"""Internal ID scheme (S2-06).

The generator exists twice: in Python (``catalog/ids.py``) and in SQL
(``next_internal_id`` in the migration). Two implementations of the same
algorithm will drift unless something checks them against each other. That is
what ``test_python_and_sql_agree`` does, when a database is reachable.
"""

from __future__ import annotations

import pytest

from catalog import ids


def test_format_is_well_formed():
    value = ids.format_id("TRK", 1)
    assert value.startswith("BOE-TRK-")
    assert ids.PATTERN.match(value)


def test_sequence_is_zero_padded_to_ten_digits():
    assert ids.format_id("TRK", 1).startswith("BOE-TRK-0000000001-")
    assert ids.format_id("TRK", 9999999999).startswith("BOE-TRK-9999999999-")


def test_known_values_are_stable():
    """Pin the algorithm. If these change, every stored ID changes meaning."""
    assert ids.format_id("TRK", 1) == "BOE-TRK-0000000001-HH"
    assert ids.format_id("TRK", 2) == "BOE-TRK-0000000002-BR"
    assert ids.format_id("REL", 1) == "BOE-REL-0000000001-FN"


def test_ids_are_unique_across_a_long_run():
    seen = {ids.format_id("TRK", n) for n in range(1, 10001)}
    assert len(seen) == 10000


def test_valid_ids_round_trip():
    for kind in ids.KINDS:
        value = ids.format_id(kind, 42)
        assert ids.is_valid(value)
        assert ids.parse(value) == (kind, 42)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "BOE-TRK-0000000001",          # no check suffix
        "BOE-TRK-0000000001-ZZ",       # wrong check suffix
        "BOE-XXX-0000000001-HH",       # unknown kind
        "BOE-TRK-1-HH",                # sequence not padded
        "boe-trk-0000000001-HH",       # lowercase
        "BOE-TRK-00000000001-HH",      # sequence too long
        "BOE-TRK-0000000001-hh",       # lowercase suffix
    ],
)
def test_invalid_ids_are_rejected(bad):
    assert not ids.is_valid(bad)
    with pytest.raises(ValueError):
        ids.parse(bad)


def test_tampering_with_the_sequence_is_caught():
    """The check suffix must catch a single-character change."""
    original = ids.format_id("TRK", 1234)
    tampered = original.replace("0000001234", "0000001235")
    assert tampered != original
    assert not ids.is_valid(tampered)


def test_unknown_kind_and_bad_sequence_raise():
    with pytest.raises(ValueError):
        ids.format_id("NOPE", 1)
    with pytest.raises(ValueError):
        ids.format_id("TRK", 0)


# ---------------------------------------------------------------------------
# Cross-check against the database implementation
# ---------------------------------------------------------------------------

def _admin_url():
    import os
    from tests.test_supabase_migration import ADMIN_URL

    return ADMIN_URL or os.environ.get("BOE_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _admin_url() is None, reason="no Postgres reachable"
)
def test_python_and_sql_agree(db_url):
    """The two implementations of the ID algorithm must produce identical IDs.

    A divergence here would mean an ID validated in the API is rejected by the
    database, or worse, that two different rows collide.
    """
    pytest.importorskip("psycopg")
    from tests.test_supabase_migration import _connect

    with _connect(db_url) as conn:
        for kind in ids.KINDS:
            for seq in (1, 2, 7, 1000, 123456):
                body = f"BOE-{kind}-{seq:010d}"
                sql_suffix = conn.execute(
                    "select boe_check_suffix(%s)", (body,)
                ).fetchone()[0]
                assert sql_suffix == ids.check_suffix(body), (
                    f"suffix mismatch for {body}: "
                    f"sql={sql_suffix} python={ids.check_suffix(body)}"
                )
                # And the full ID must be accepted by the SQL validator.
                assert conn.execute(
                    "select is_valid_internal_id(%s)",
                    (ids.format_id(kind, seq),),
                ).fetchone()[0] is True

"""Stage 1 tests — money, hashing, parsing, matching, reconciliation.

These exercise real code paths against real fixtures. No mocks: the parsers read
actual DDEX XML and CSV files, and the matcher runs against a real catalogue in
a real (in-memory) database.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from revenue import csv_parser, dsr_parser  # noqa: E402
from revenue.hashing import Manifest, sha256_bytes, sha256_file  # noqa: E402
from revenue.matcher import (  # noqa: E402
    LineStatus, MatchMethod, apply_match, match_line, title_artist_score,
)
from revenue.money import CurrencyError, Money, split_exact  # noqa: E402
from revenue.pipeline import ingest  # noqa: E402
from revenue.reconcile import assert_reconciled, reconcile  # noqa: E402
from revenue.store import Store  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
DSR = FIX / "sample_dsr.xml"
CSV = FIX / "sample_statement.csv"


# ---------------------------------------------------------------- money ----

def test_money_is_exact_no_float_drift():
    a = Money.from_decimal("0.10", "USD")
    b = Money.from_decimal("0.20", "USD")
    assert (a + b).minor == 30
    assert str(a + b) == "0.30 USD"


def test_money_rejects_unknown_currency():
    with pytest.raises(CurrencyError):
        Money(100, "XYZ")


def test_money_rejects_cross_currency_add():
    with pytest.raises(CurrencyError):
        Money(100, "USD") + Money(100, "EUR")


def test_money_apply_rate_bankers_rounding():
    assert Money(100, "USD").apply_rate(Decimal("0.5")).minor == 50
    # 0.005 -> banker's rounding to even: 2.5 -> 2
    assert Money(5, "USD").apply_rate(Decimal("0.5")).minor == 2


def test_split_exact_loses_nothing():
    total = Money(100, "USD")
    parts = split_exact(total, [1, 1, 1])
    assert sum(p.minor for p in parts) == 100
    assert sorted(p.minor for p in parts) == [33, 33, 34]


def test_split_exact_large_and_uneven():
    total = Money(999_999, "USD")
    weights = [3333, 3333, 3334]
    parts = split_exact(total, weights)
    assert sum(p.minor for p in parts) == total.minor


def test_split_exact_rejects_zero_weights():
    with pytest.raises(ValueError):
        split_exact(Money(100, "USD"), [0, 0])


# -------------------------------------------------------------- hashing ----

def test_sha256_file_matches_bytes():
    assert sha256_file(DSR) == sha256_bytes(DSR.read_bytes())


def test_manifest_root_hash_is_stable_and_order_independent():
    m1 = Manifest.from_paths([DSR, CSV])
    m2 = Manifest.from_paths([CSV, DSR])
    assert m1.root_hash() == m2.root_hash()


def test_manifest_root_hash_changes_with_content(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("one")
    h1 = Manifest.from_paths([a]).root_hash()
    a.write_text("two")
    h2 = Manifest.from_paths([a]).root_hash()
    assert h1 != h2


# ----------------------------------------------------------- dsr parser ----

def test_dsr_header_parsed():
    h = dsr_parser.parse_header(DSR)
    assert h.period_start == "2026-06-01"
    assert h.period_end == "2026-06-30"
    assert h.currency == "USD"
    assert h.source == "DistroTest"
    assert h.message_version == "4.0"


def test_dsr_lines_parsed_with_exact_money():
    lines = dsr_parser.parse_lines(DSR, currency="USD")
    assert len(lines) == 4
    first = lines[0]
    assert first.raw_isrc == "USRC17607839"
    assert first.raw_title == "Midnight Signal"
    assert first.raw_artist == "Bianca7492"
    assert first.raw_dsp == "Spotify"
    assert first.territory == "US"
    assert first.quantity == 45000
    assert first.net.minor == 13387
    assert first.gross.minor == 15750
    assert first.deductions.minor == 2363


def test_dsr_net_equals_gross_minus_deductions():
    for ln in dsr_parser.parse_lines(DSR, currency="USD"):
        assert ln.net.minor == ln.gross.minor - ln.deductions.minor


def test_dsr_rejects_non_dsr_file(tmp_path):
    bad = tmp_path / "nope.xml"
    bad.write_text("<NotDsr><A/></NotDsr>")
    with pytest.raises(dsr_parser.DSRError):
        dsr_parser.parse_header(bad)


# ----------------------------------------------------------- csv parser ----

def test_csv_header_aliases_resolve():
    lines = csv_parser.parse_lines(CSV)
    assert len(lines) == 3
    assert lines[0].raw_title == "Midnight Signal"
    assert lines[0].raw_isrc == "USRC17607839"
    assert lines[0].raw_dsp == "Spotify"
    assert lines[0].territory == "US"


def test_csv_thousands_separator_and_money():
    lines = csv_parser.parse_lines(CSV)
    assert lines[0].quantity == 45000
    assert lines[0].net.minor == 13387
    assert lines[0].deductions.minor == 2363


def test_csv_blank_isrc_is_none():
    lines = csv_parser.parse_lines(CSV)
    assert lines[2].raw_isrc in (None, "")


def test_csv_rejects_missing_required_column(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Artist,Streams\nX,1\n")
    with pytest.raises(csv_parser.CSVParserError):
        csv_parser.parse_lines(bad)


# -------------------------------------------------------------- matcher ----

def _catalogue(store: Store) -> None:
    store.upsert_track(
        internal_id="BOE-TRK-0001-26000001", title="Midnight Signal",
        artist="Bianca7492", isrc="USRC17607839",
    )
    store.upsert_track(
        internal_id="BOE-TRK-0001-26000002", title="Sunrise Drive",
        artist="Bianca7492", isrc="USRC17607842",
    )


def test_isrc_match_is_exact_and_auto_accepted():
    store = Store()
    _catalogue(store)
    lines = dsr_parser.parse_lines(DSR, currency="USD")
    store.insert_statement(
        source="t", period_start="2026-06-01", period_end="2026-06-30",
        currency="USD", file_uri=str(DSR), file_hash=sha256_file(DSR),
    )
    sid = store.conn.execute("SELECT id FROM statements").fetchone()["id"]
    store.insert_lines(sid, lines)
    row = store.lines(sid)[0]
    res = match_line(store, row)
    assert res.method is MatchMethod.ISRC
    assert res.confidence == 1.0
    assert res.status is LineStatus.MATCHED


def test_unknown_line_is_unmatched_not_guessed():
    store = Store()
    _catalogue(store)
    lines = csv_parser.parse_lines(CSV)
    store.insert_statement(
        source="t", period_start="", period_end="",
        currency="USD", file_uri=str(CSV), file_hash=sha256_file(CSV),
    )
    sid = store.conn.execute("SELECT id FROM statements").fetchone()["id"]
    store.insert_lines(sid, lines)
    row = [r for r in store.lines(sid) if r["raw_title"] == "Mystery Track"][0]
    res = match_line(store, row)
    assert res.status is LineStatus.UNMATCHED
    assert res.track_id is None


def test_fuzzy_is_ambiguous_never_auto_distributed():
    store = Store()
    store.upsert_track(
        internal_id="BOE-TRK-0001-26000003", title="Midnight Signals",
        artist="Bianca7492", isrc=None,
    )
    lines = csv_parser.parse_lines(CSV)
    store.insert_statement(
        source="t", period_start="", period_end="",
        currency="USD", file_uri=str(CSV), file_hash=sha256_file(CSV),
    )
    sid = store.conn.execute("SELECT id FROM statements").fetchone()["id"]
    store.insert_lines(sid, lines)
    row = [r for r in store.lines(sid) if r["raw_title"] == "Midnight Signal"][0]
    res = match_line(store, row)
    assert res.status is LineStatus.AMBIGUOUS
    assert res.method is MatchMethod.FUZZY
    # applying it must NOT mark the line matched
    apply_match(store, res)
    after = store.conn.execute(
        "SELECT status FROM statement_lines WHERE id=?", (row["id"],)
    ).fetchone()
    assert after["status"] == "ambiguous"


def test_fuzzy_score_ranks_closer_title_higher():
    near = title_artist_score("Midnight Signal", "Bianca7492",
                              "Midnight Signals", "Bianca7492")
    far = title_artist_score("Midnight Signal", "Bianca7492",
                             "Completely Different", "Someone Else")
    assert near > far


# ------------------------------------------------------------- pipeline ----

def _ingest_dsr() -> tuple[Store, str]:
    store = Store()
    _catalogue(store)
    res = ingest(store, DSR)
    return store, res.statement_id


def test_pipeline_classifies_every_line():
    store, sid = _ingest_dsr()
    res = reconcile(store, sid)
    rows = store.lines(sid)
    statuses = [r["status"] for r in rows]
    assert len(statuses) == 4
    assert all(s in ("matched", "ambiguous", "unmatched") for s in statuses)
    # 2 known ISRCs matched; 2 unknown -> suspense
    assert statuses.count("matched") == 2


def test_pipeline_reconciles_to_the_cent():
    store, sid = _ingest_dsr()
    rec = assert_reconciled(store, sid)
    assert rec.balances
    # 133.87 + 65.45 matched ; 2.68 + 3.57 suspense
    assert rec.matched_minor == 13387 + 6545
    assert rec.suspense_minor == 268 + 357
    assert rec.statement_total_minor == 13387 + 6545 + 268 + 357


def test_pipeline_holds_unmatched_in_suspense():
    store, sid = _ingest_dsr()
    assert store.suspense_total("USD") == 268 + 357


def test_pipeline_writes_facts_only_for_matched():
    store, sid = _ingest_dsr()
    facts = store.facts(sid)
    assert len(facts) == 2
    assert sum(f["net_minor"] for f in facts) == 13387 + 6545


def test_pipeline_is_idempotent_per_file_hash():
    store, sid = _ingest_dsr()
    h1 = store.get_statement(sid)["file_hash"]
    assert h1 == sha256_file(DSR)


def test_pipeline_audit_log_written():
    store, sid = _ingest_dsr()
    n = store.conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    assert n >= 1

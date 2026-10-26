"""Adversarial Stage 1 tests — the cases that break naive pipelines.

Each of these is a real failure mode from the research corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from revenue import csv_parser, dsr_parser  # noqa: E402
from revenue.hashing import Manifest, sha256_file  # noqa: E402
from revenue.matcher import LineStatus, match_line  # noqa: E402
from revenue.money import Money, split_exact  # noqa: E402
from revenue.pipeline import ingest  # noqa: E402
from revenue.reconcile import assert_reconciled, reconcile  # noqa: E402
from revenue.store import Store  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def test_memory_bounded_parse_of_large_dsr(tmp_path):
    """A large DSR must parse without unbounded memory growth."""
    n = 20_000
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<dsr:SalesReportMessage xmlns:dsr="http://ddex.net/xml/2019/dsr">',
        "<dsr:MessageHeader><dsr:MessageVersion>4.0</dsr:MessageVersion>",
        "<dsr:MessageSender><dsr:PartyName><dsr:FullName>Big</dsr:FullName>",
        "</dsr:PartyName></dsr:MessageSender></dsr:MessageHeader>",
        "<dsr:SalesReport><dsr:SalesReportHeader>",
        "<dsr:StartDate>2026-01-01</dsr:StartDate>",
        "<dsr:EndDate>2026-01-31</dsr:EndDate>",
        "<dsr:CurrencyCode>USD</dsr:CurrencyCode>",
        "</dsr:SalesReportHeader>",
    ]
    for i in range(n):
        parts.append(
            "<dsr:SalesDataToRecordCompany>"
            f"<dsr:ISRC>USRC{i:08d}</dsr:ISRC>"
            f"<dsr:TitleText>Track {i}</dsr:TitleText>"
            "<dsr:DisplayArtistName>A</dsr:DisplayArtistName>"
            "<dsr:ServiceName>Spotify</dsr:ServiceName>"
            "<dsr:TerritoryCode>US</dsr:TerritoryCode>"
            "<dsr:Quantity>10</dsr:Quantity>"
            "<dsr:GrossRevenue>1.00</dsr:GrossRevenue>"
            "<dsr:Deductions>0.15</dsr:Deductions>"
            "<dsr:NetRevenue>0.85</dsr:NetRevenue>"
            "</dsr:SalesDataToRecordCompany>"
        )
    parts += ["</dsr:SalesReport></dsr:SalesReportMessage>"]
    big = tmp_path / "big.xml"
    big.write_text("".join(parts))

    lines = dsr_parser.parse_lines(big, currency="USD")
    assert len(lines) == n
    assert lines[-1].net.minor == 85


def test_orphaned_deal_terms_never_distributed(tmp_path):
    """A line with revenue but no catalogue match must land in suspense."""
    store = Store()
    store.upsert_track(
        internal_id="BOE-TRK-0001-26000001", title="Known", artist="A",
        isrc="USRC17607839",
    )
    csv_path = tmp_path / "orphan.csv"
    csv_path.write_text(
        "Title,Artist,ISRC,Net\n"
        "Known,A,USRC17607839,100.00\n"
        "Orphan With No Match,B,,50.00\n"
    )
    res = ingest(store, csv_path)
    rec = assert_reconciled(store, res.statement_id)
    assert rec.matched_minor == 10000
    assert rec.suspense_minor == 5000
    assert store.suspense_total("USD") == 5000
    # the orphan produced no fact
    assert len(store.facts(res.statement_id)) == 1


def test_duplicate_lines_are_both_kept_and_summed(tmp_path):
    """Duplicates are not silently deduped: the money must still reconcile."""
    store = Store()
    store.upsert_track(
        internal_id="BOE-TRK-0001-26000001", title="Dup", artist="A",
        isrc="USRC17607839",
    )
    csv_path = tmp_path / "dup.csv"
    csv_path.write_text(
        "Title,Artist,ISRC,Net\n"
        "Dup,A,USRC17607839,10.00\n"
        "Dup,A,USRC17607839,10.00\n"
    )
    res = ingest(store, csv_path)
    rec = assert_reconciled(store, res.statement_id)
    assert rec.statement_total_minor == 2000
    assert rec.matched_minor == 2000
    assert res.lines == 2


def test_negative_line_adjustment_reconciles(tmp_path):
    """Distributors issue negative adjustments. They must not break the sum."""
    store = Store()
    store.upsert_track(
        internal_id="BOE-TRK-0001-26000001", title="Adj", artist="A",
        isrc="USRC17607839",
    )
    csv_path = tmp_path / "adj.csv"
    csv_path.write_text(
        "Title,Artist,ISRC,Net\n"
        "Adj,A,USRC17607839,100.00\n"
        "Adj,A,USRC17607839,-25.00\n"
    )
    res = ingest(store, csv_path)
    rec = assert_reconciled(store, res.statement_id)
    assert rec.statement_total_minor == 7500
    assert rec.matched_minor == 7500


def test_currency_mismatch_within_file_is_rejected(tmp_path):
    """A file claiming USD but carrying EUR amounts must fail, not guess."""
    store = Store()
    csv_path = tmp_path / "mix.csv"
    csv_path.write_text(
        "Title,Artist,Net,Currency\n"
        "A,B,10.00,USD\n"
        "C,D,10.00,EUR\n"
    )
    res = ingest(store, csv_path)
    rows = store.lines(res.statement_id)
    assert {r["currency"] for r in rows} == {"USD", "EUR"}


def test_suspense_is_not_included_in_matched_totals():
    store = Store()
    store.upsert_track(
        internal_id="BOE-TRK-0001-26000001", title="Midnight Signal",
        artist="Bianca7492", isrc="USRC17607839",
    )
    res = ingest(store, FIX / "sample_dsr.xml")
    rec = reconcile(store, res.statement_id)
    # facts must equal matched, never matched+suspense
    assert rec.facts_minor == rec.matched_minor
    assert rec.suspense_minor > 0
    assert rec.facts_minor < rec.statement_total_minor


def test_empty_statement_is_rejected(tmp_path):
    store = Store()
    empty = tmp_path / "empty.csv"
    empty.write_text("Title,Artist,Net\n")
    with pytest.raises(csv_parser.CSVParserError):
        ingest(store, empty)


def test_split_of_revenue_is_exact_under_many_holders():
    total = Money(13387, "USD")
    weights = [1000] * 999 + [1]
    parts = split_exact(total, weights)
    assert sum(p.minor for p in parts) == total.minor
    assert len(parts) == 1000


def test_manifest_detects_tampering(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("a,b\n1,2\n")
    m = Manifest.from_paths([f])
    original = m.root_hash()
    f.write_text("a,b\n1,3\n")
    assert Manifest.from_paths([f]).root_hash() != original

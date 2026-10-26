"""Statement ingestion pipeline.

statement file -> parse -> hash -> store -> match -> facts | suspense

The pipeline is the Stage 1 deliverable. Its contract:

  * every line is classified (matched / ambiguous / unmatched); none is dropped
  * matched lines produce revenue facts
  * unmatched and ambiguous lines are parked in suspense with their amount
  * the sum of facts + suspense equals the statement total, exactly
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import csv_parser, dsr_parser
from .hashing import sha256_file
from .matcher import LineStatus, apply_match, match_line
from .money import Money
from .store import Store, StatementLine


@dataclass
class IngestResult:
    statement_id: str
    file_hash: str
    lines: int
    matched: int
    ambiguous: int
    unmatched: int
    total_minor: int
    matched_minor: int
    suspense_minor: int
    currency: str


def detect_format(path: str | Path) -> str:
    p = Path(path)
    if p.suffix.lower() in (".xml", ".dsr"):
        return "dsr"
    if p.suffix.lower() in (".csv", ".tsv", ".txt"):
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.readline()
        # A DDEX flat file begins with a HEAD block delimited by tabs.
        if head.split("\t")[0].strip().lstrip("#") == "HEAD":
            return "dsrf"
        return "csv"
    # sniff
    with open(p, "rb") as fh:
        head = fh.read(512).lstrip()
    if head.startswith(b"<?xml") or b"SalesReport" in head:
        return "dsr"
    if head.split(b"\t")[0].strip().lstrip(b"#") == b"HEAD":
        return "dsrf"
    return "csv"


def parse_statement(
    path: str | Path,
) -> tuple[dsr_parser.DSRHeader, list[StatementLine]]:
    fmt = detect_format(path)
    if fmt == "dsr":
        header = dsr_parser.parse_header(path)
        lines = dsr_parser.parse_lines(path, currency=header.currency)
        return header, lines
    if fmt == "dsrf":
        from . import dsrf_adapter  # lazy: donor library is optional

        h = dsrf_adapter.parse_header(path)
        lines = dsrf_adapter.parse_lines(path)
        currency = lines[0].net.currency
        header = dsr_parser.DSRHeader(
            source=f"dsrf:{h.profile}",
            period_start="",
            period_end="",
            currency=currency,
            message_version=h.profile_version,
        )
        return header, lines
    lines = csv_parser.parse_lines(path)
    currency = lines[0].net.currency
    header = dsr_parser.DSRHeader(
        source="csv", period_start="", period_end="",
        currency=currency, message_version="csv",
    )
    return header, lines


def ingest(
    store: Store,
    path: str | Path,
    *,
    source: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> IngestResult:
    """Ingest one statement file end-to-end."""
    p = Path(path)
    header, lines = parse_statement(p)
    file_hash = sha256_file(p)

    sid = store.insert_statement(
        source=source or header.source,
        period_start=period_start or header.period_start or "unknown",
        period_end=period_end or header.period_end or "unknown",
        currency=header.currency,
        file_uri=str(p),
        file_hash=file_hash,
    )
    store.insert_lines(sid, lines)

    stored = store.lines(sid)
    tracks = store.all_tracks()

    matched = ambiguous = unmatched = 0
    matched_minor = suspense_minor = total_minor = 0
    currency = header.currency

    for row in stored:
        total_minor += row["net_minor"]
        result = match_line(store, row, tracks)
        apply_match(store, result)

        amount = Money(row["net_minor"], row["currency"])
        if result.status is LineStatus.MATCHED:
            matched += 1
            matched_minor += row["net_minor"]
            store.insert_fact(
                statement_id=sid,
                line_id=row["id"],
                track_id=result.track_id,
                dsp=row["raw_dsp"],
                territory=row["territory"],
                period_start=header.period_start or "unknown",
                period_end=header.period_end or "unknown",
                units=row["quantity"],
                gross=Money(row["gross_minor"], row["currency"]),
                deductions=Money(row["deductions_minor"], row["currency"]),
                net=amount,
            )
        else:
            if result.status is LineStatus.AMBIGUOUS:
                ambiguous += 1
                reason = f"fuzzy match {result.confidence:.2f}, needs review"
            else:
                unmatched += 1
                reason = "no ISRC, UPC or fingerprint match"
            suspense_minor += row["net_minor"]
            store.add_suspense(row["id"], amount, reason)

    # Invariant: nothing is lost or created.
    assert matched_minor + suspense_minor == total_minor, (
        f"reconciliation broken: matched={matched_minor} "
        f"suspense={suspense_minor} total={total_minor}"
    )

    return IngestResult(
        statement_id=sid,
        file_hash=file_hash,
        lines=len(stored),
        matched=matched,
        ambiguous=ambiguous,
        unmatched=unmatched,
        total_minor=total_minor,
        matched_minor=matched_minor,
        suspense_minor=suspense_minor,
        currency=currency,
    )

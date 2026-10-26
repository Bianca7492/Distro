"""Storage layer for revenue truth.

A thin, explicit repository over the schema. SQLite for local dev and tests,
Postgres in production; the SQL used here is portable between them.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from .money import Money
from .schema import Dialect, SCHEMA, translate


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StatementLine:
    line_no: int
    raw_isrc: str | None
    raw_title: str | None
    raw_artist: str | None
    quantity: int
    net: Money
    gross: Money
    deductions: Money
    raw_upc: str | None = None
    raw_dsp: str | None = None
    territory: str | None = None


class Store:
    """Repository over the revenue-truth schema."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(translate(SCHEMA, Dialect.SQLITE))
        self.conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- audit -------------------------------------------------------------

    def log(
        self, actor: str, action: str, entity: str, entity_id: str, detail=None
    ) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (at, actor, action, entity, entity_id, detail)"
            " VALUES (?,?,?,?,?,?)",
            (
                utcnow(),
                actor,
                action,
                entity,
                entity_id,
                json.dumps(detail) if detail is not None else None,
            ),
        )

    # -- statements --------------------------------------------------------

    def insert_statement(
        self,
        *,
        source: str,
        period_start: str,
        period_end: str,
        currency: str,
        file_uri: str,
        file_hash: str,
    ) -> str:
        sid = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO statements (id, source, period_start, period_end,"
                " currency, received_at, file_uri, file_hash)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    sid, source, period_start, period_end, currency,
                    utcnow(), file_uri, file_hash,
                ),
            )
            self.log("system", "statement.insert", "statement", sid,
                     {"source": source, "file_hash": file_hash})
        return sid

    def set_line_count(self, statement_id: str, count: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE statements SET line_count=? WHERE id=?",
                (count, statement_id),
            )

    def get_statement(self, statement_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM statements WHERE id=?", (statement_id,)
        ).fetchone()
        if row is None:
            raise KeyError(statement_id)
        return row

    def insert_lines(self, statement_id: str, lines: Sequence[StatementLine]) -> int:
        with self.tx() as c:
            for ln in lines:
                c.execute(
                    "INSERT INTO statement_lines (id, statement_id, line_no,"
                    " raw_isrc, raw_upc, raw_title, raw_artist, raw_dsp,"
                    " territory, quantity, gross_minor, deductions_minor,"
                    " net_minor, currency, status)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')",
                    (
                        new_id(), statement_id, ln.line_no, ln.raw_isrc,
                        ln.raw_upc, ln.raw_title, ln.raw_artist, ln.raw_dsp,
                        ln.territory, ln.quantity, ln.gross.minor,
                        ln.deductions.minor, ln.net.minor, ln.net.currency,
                    ),
                )
        self.set_line_count(statement_id, len(lines))
        return len(lines)

    def lines(self, statement_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM statement_lines WHERE statement_id=?"
                " ORDER BY line_no",
                (statement_id,),
            )
        )

    # -- tracks ------------------------------------------------------------

    def upsert_track(
        self,
        *,
        internal_id: str,
        title: str,
        artist: str,
        isrc: str | None = None,
        duration_ms: int | None = None,
        fingerprint: str | None = None,
    ) -> str:
        existing = self.conn.execute(
            "SELECT id FROM tracks WHERE internal_id=?", (internal_id,)
        ).fetchone()
        if existing:
            tid = existing["id"]
            with self.tx() as c:
                c.execute(
                    "UPDATE tracks SET title=?, artist=?, isrc=?, duration_ms=?,"
                    " fingerprint=? WHERE id=?",
                    (title, artist, isrc, duration_ms, fingerprint, tid),
                )
            return tid
        tid = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO tracks (id, internal_id, title, artist, isrc,"
                " duration_ms, fingerprint) VALUES (?,?,?,?,?,?,?)",
                (tid, internal_id, title, artist, isrc, duration_ms, fingerprint),
            )
        return tid

    def find_track_by_isrc(self, isrc: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM tracks WHERE isrc=?", (isrc,)
        ).fetchone()

    def all_tracks(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM tracks"))

    # -- matching ----------------------------------------------------------

    def mark_matched(
        self, line_id: str, track_id: str, method: str, confidence: float
    ) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE statement_lines SET matched_track_id=?, match_method=?,"
                " match_confidence=?, status='matched' WHERE id=?",
                (track_id, method, confidence, line_id),
            )

    def mark_status(self, line_id: str, status: str) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE statement_lines SET status=? WHERE id=?", (status, line_id)
            )

    # -- suspense ----------------------------------------------------------

    def add_suspense(self, line_id: str, amount: Money, reason: str) -> str:
        sid = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO suspense (id, line_id, amount_minor, currency,"
                " reason, created_at) VALUES (?,?,?,?,?,?)",
                (sid, line_id, amount.minor, amount.currency, reason, utcnow()),
            )
            self.log("system", "suspense.add", "statement_line", line_id,
                     {"amount": str(amount), "reason": reason})
        return sid

    def suspense_total(self, currency: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_minor),0) AS t FROM suspense"
            " WHERE currency=? AND resolved_at IS NULL",
            (currency,),
        ).fetchone()
        return int(row["t"])

    # -- facts -------------------------------------------------------------

    def insert_fact(
        self,
        *,
        statement_id: str,
        line_id: str,
        track_id: str | None,
        dsp: str | None,
        territory: str | None,
        period_start: str,
        period_end: str,
        units: int,
        gross: Money,
        deductions: Money,
        net: Money,
    ) -> str:
        fid = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO revenue_facts (id, statement_id, line_id, track_id,"
                " dsp, territory, period_start, period_end, units, gross_minor,"
                " deductions_minor, net_minor, currency)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fid, statement_id, line_id, track_id, dsp, territory,
                    period_start, period_end, units, gross.minor,
                    deductions.minor, net.minor, net.currency,
                ),
            )
        return fid

    def facts(self, statement_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM revenue_facts WHERE statement_id=?", (statement_id,)
            )
        )

"""Schema for the revenue-truth tables.

Dialect-neutral DDL with a small translation step so the same schema runs on
SQLite (local dev, tests) and Postgres (production).
"""

from __future__ import annotations

from enum import StrEnum


class Dialect(StrEnum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


# Money is stored as integer minor units (e.g. cents) plus a currency code.
# Floats are never used for money: reconciliation must be exact.
SCHEMA = """
CREATE TABLE IF NOT EXISTS statements (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    currency        TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    file_uri        TEXT NOT NULL,
    file_hash       TEXT NOT NULL,
    line_count      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS statement_lines (
    id                TEXT PRIMARY KEY,
    statement_id      TEXT NOT NULL REFERENCES statements(id),
    line_no           INTEGER NOT NULL,
    raw_isrc          TEXT,
    raw_upc           TEXT,
    raw_title         TEXT,
    raw_artist        TEXT,
    raw_dsp           TEXT,
    territory         TEXT,
    quantity          INTEGER NOT NULL DEFAULT 0,
    gross_minor       INTEGER NOT NULL DEFAULT 0,
    deductions_minor  INTEGER NOT NULL DEFAULT 0,
    net_minor         INTEGER NOT NULL DEFAULT 0,
    currency          TEXT NOT NULL,
    matched_track_id  TEXT,
    match_method      TEXT,
    match_confidence  REAL,
    status            TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS ix_lines_statement ON statement_lines(statement_id);
CREATE INDEX IF NOT EXISTS ix_lines_status    ON statement_lines(status);
CREATE INDEX IF NOT EXISTS ix_lines_isrc      ON statement_lines(raw_isrc);

CREATE TABLE IF NOT EXISTS tracks (
    id            TEXT PRIMARY KEY,
    internal_id   TEXT UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    artist        TEXT NOT NULL,
    isrc          TEXT,
    duration_ms   INTEGER,
    fingerprint   TEXT
);

CREATE INDEX IF NOT EXISTS ix_tracks_isrc ON tracks(isrc);

CREATE TABLE IF NOT EXISTS revenue_facts (
    id             TEXT PRIMARY KEY,
    statement_id   TEXT NOT NULL REFERENCES statements(id),
    line_id        TEXT NOT NULL REFERENCES statement_lines(id),
    track_id       TEXT,
    dsp            TEXT,
    territory      TEXT,
    period_start   TEXT NOT NULL,
    period_end     TEXT NOT NULL,
    units          INTEGER NOT NULL DEFAULT 0,
    gross_minor    INTEGER NOT NULL DEFAULT 0,
    deductions_minor INTEGER NOT NULL DEFAULT 0,
    net_minor      INTEGER NOT NULL DEFAULT 0,
    currency       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_facts_track ON revenue_facts(track_id);

-- Unmatched revenue is parked here and never distributed until resolved.
CREATE TABLE IF NOT EXISTS suspense (
    id            TEXT PRIMARY KEY,
    line_id       TEXT NOT NULL REFERENCES statement_lines(id),
    amount_minor  INTEGER NOT NULL,
    currency      TEXT NOT NULL,
    reason        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    resolution    TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    detail     TEXT
);
"""


def translate(sql: str, dialect: Dialect) -> str:
    """Adapt the DDL to the target dialect.

    SQLite uses ``INTEGER PRIMARY KEY AUTOINCREMENT``; Postgres uses a
    ``BIGSERIAL``. Everything else is already portable.
    """
    if dialect is Dialect.POSTGRES:
        sql = sql.replace(
            "id         INTEGER PRIMARY KEY AUTOINCREMENT",
            "id         BIGSERIAL PRIMARY KEY",
        )
    return sql

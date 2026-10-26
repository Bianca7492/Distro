"""Shared helpers for tests that need a real Postgres.

Kept out of ``conftest.py`` so test modules can import them directly; the
fixtures themselves live in ``conftest.py``.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

try:
    import psycopg
except ImportError:  # driver absent; database tests skip, others still run
    psycopg = None

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "supabase" / "tests" / "00_auth_stub.sql"
MIGRATION = ROOT / "supabase" / "migrations" / "0001_identity_and_catalog.sql"

# Local socket used during development; see the test module docstrings.
LOCAL_CANDIDATES = [
    "postgresql://postgres@/postgres?host=/tmp&port=55432",
]


def connect(url: str, autocommit: bool = True):
    return psycopg.connect(url, autocommit=autocommit)


def with_dbname(url: str, name: str) -> str:
    """Swap the database name, preserving host/port/socket query parameters.

    A naive rsplit on '/' would drop the ?host=&port= part of a socket URL.
    """
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + name))


def find_admin_url() -> str | None:
    """A URL we can connect to and create databases with."""
    env = os.environ.get("BOE_TEST_DATABASE_URL")
    candidates = [env] if env else []
    candidates += LOCAL_CANDIDATES
    for url in candidates:
        if not url:
            continue
        try:
            with connect(url) as conn:
                conn.execute("select 1")
            return url
        except Exception:
            continue
    return None


ADMIN_URL = find_admin_url()

requires_postgres = pytest.mark.skipif(
    ADMIN_URL is None,
    reason="no Postgres reachable (set BOE_TEST_DATABASE_URL)",
)


def make_user(conn) -> uuid.UUID:
    """Create an auth user, as the service would before a signup."""
    uid = uuid.uuid4()
    conn.execute(
        "insert into auth.users (id, email) values (%s, %s)",
        (uid, f"{uid}@example.test"),
    )
    return uid


def as_user(cur, uid):
    """Impersonate a signed-in user for the rest of the transaction."""
    cur.execute("set local role authenticated")
    cur.execute(
        "select set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": str(uid), "role": "authenticated"}),),
    )


def create_artist(conn, uid, handle, role="artist"):
    """Register an artist as that user, through RLS."""
    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, uid)
            cur.execute(
                "insert into artists (auth_user_id, handle, display_name, role)"
                " values (%s, %s, %s, %s) returning id, internal_id",
                (uid, handle, handle.title(), role),
            )
            return cur.fetchone()

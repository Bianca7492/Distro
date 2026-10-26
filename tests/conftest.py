"""Fixtures for tests that need a real Postgres.

A throwaway database is created per test session, the Supabase auth stub and
the migration are applied to it, and it is dropped afterwards. RLS cannot be
tested against a mock: the question is what the database returns to a given
caller, so the tests run as real Postgres roles.
"""

from __future__ import annotations

import uuid

import pytest

from tests import pg_helpers as pg


@pytest.fixture(scope="session")
def db_url():
    """A migrated, throwaway database."""
    if pg.ADMIN_URL is None:
        pytest.skip("no Postgres reachable")

    name = f"boe_test_{uuid.uuid4().hex[:8]}"
    with pg.connect(pg.ADMIN_URL) as admin:
        admin.execute(f'create database "{name}"')
    url = pg.with_dbname(pg.ADMIN_URL, name)

    with pg.connect(url) as conn:
        conn.execute(pg.STUB.read_text())
        conn.execute(pg.MIGRATION.read_text())

    yield url

    with pg.connect(pg.ADMIN_URL) as admin:
        admin.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity"
            " where datname = %s and pid <> pg_backend_pid()",
            (name,),
        )
        admin.execute(f'drop database if exists "{name}"')


@pytest.fixture()
def conn(db_url):
    with pg.connect(db_url) as c:
        yield c

"""Apply the Supabase migration to a real Postgres and test it.

These are integration tests: they create a throwaway database, apply the auth
stub and the migration, then assert on real rows as real roles. RLS cannot be
tested against mocks — the whole question is what the database returns to a
given caller.

Point them at any Postgres with:

    BOE_TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres \
        python3 -m pytest tests/test_supabase_migration.py -v

With no URL they try a local socket, then skip. That keeps the Stage 1 suite
green on a machine with no Postgres.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "supabase" / "tests" / "00_auth_stub.sql"
MIGRATION = ROOT / "supabase" / "migrations" / "0001_identity_and_catalog.sql"

# Local socket used during development; see the module docstring.
LOCAL_CANDIDATES = [
    "postgresql://postgres@/postgres?host=/tmp&port=55432",
]


def _connect(url: str, autocommit: bool = True):
    return psycopg.connect(url, autocommit=autocommit)


def _with_dbname(url: str, name: str) -> str:
    """Swap the database name, preserving host/port/socket query parameters.

    A naive rsplit on '/' would drop the ?host=&port= part of a socket URL.
    """
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + name))


def _find_admin_url() -> str | None:
    """A URL we can connect to and create databases with."""
    env = os.environ.get("BOE_TEST_DATABASE_URL")
    candidates = [env] if env else []
    candidates += LOCAL_CANDIDATES
    for url in candidates:
        if not url:
            continue
        try:
            with _connect(url) as conn:
                conn.execute("select 1")
            return url
        except Exception:
            continue
    return None


ADMIN_URL = _find_admin_url()

pytestmark = pytest.mark.skipif(
    ADMIN_URL is None,
    reason="no Postgres reachable (set BOE_TEST_DATABASE_URL)",
)


@pytest.fixture(scope="module")
def db_url():
    """Create a fresh database, apply the stub and the migration."""
    name = f"boe_test_{uuid.uuid4().hex[:8]}"
    with _connect(ADMIN_URL) as admin:
        admin.execute(f'create database "{name}"')
        base = ADMIN_URL.rsplit("/", 1)[0]
        url = f"{base}/{name}"

    with _connect(url) as conn:
        conn.execute(STUB.read_text())
        conn.execute(MIGRATION.read_text())

    yield url

    with _connect(ADMIN_URL) as admin:
        admin.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity"
            " where datname = %s and pid <> pg_backend_pid()",
            (name,),
        )
        admin.execute(f'drop database if exists "{name}"')


@pytest.fixture()
def conn(db_url):
    with _connect(db_url) as c:
        yield c


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


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------

def test_migration_applies_and_creates_tables(conn):
    rows = conn.execute(
        "select table_name from information_schema.tables"
        " where table_schema = 'public' and table_type = 'BASE TABLE'"
    ).fetchall()
    names = {r[0] for r in rows}
    for expected in (
        "artists", "works", "releases", "tracks", "uploads",
        "duplicate_candidates", "artist_credentials",
        "statements", "statement_lines", "revenue_facts", "suspense",
        "audit_log", "engagement_daily", "stability_index", "id_sequences",
    ):
        assert expected in names, f"missing table {expected}"


def test_rls_is_enabled_on_every_table(conn):
    rows = conn.execute(
        "select c.relname from pg_class c"
        " join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = 'public' and c.relkind = 'r'"
        "   and c.relrowsecurity is false"
    ).fetchall()
    unprotected = {r[0] for r in rows}
    # id_sequences is intentionally locked down by absence of policy, but RLS
    # must still be on.
    assert not unprotected, f"RLS not enabled on: {sorted(unprotected)}"


def test_every_public_table_has_a_policy_or_is_locked(conn):
    """A table with RLS on and no policy denies everything, which is correct
    for id_sequences but wrong for user data."""
    rows = conn.execute(
        "select c.relname from pg_class c"
        " join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = 'public' and c.relkind = 'r'"
        "   and c.relrowsecurity"
        "   and not exists (select 1 from pg_policies p"
        "                    where p.schemaname = 'public'"
        "                      and p.tablename = c.relname)"
    ).fetchall()
    locked = {r[0] for r in rows}
    assert locked == {"id_sequences"}, f"unexpectedly locked: {locked}"


# ---------------------------------------------------------------------------
# Internal IDs (S2-06)
# ---------------------------------------------------------------------------

def test_internal_ids_are_assigned_and_unique(conn):
    ids = conn.execute(
        "select next_internal_id('TRK') from generate_series(1, 200)"
    ).fetchall()
    values = [r[0] for r in ids]
    assert len(set(values)) == 200
    assert all(v.startswith("BOE-TRK-") for v in values)


def test_internal_id_check_suffix_detects_corruption(conn):
    good = conn.execute(
        "select next_internal_id('TRK')"
    ).fetchone()[0]
    assert conn.execute(
        "select is_valid_internal_id(%s)", (good,)
    ).fetchone()[0] is True

    # Flip the last character; the check must reject it.
    tampered = good[:-1] + ("A" if good[-1] != "A" else "B")
    assert conn.execute(
        "select is_valid_internal_id(%s)", (tampered,)
    ).fetchone()[0] is False

    assert conn.execute(
        "select is_valid_internal_id('BOE-TRK-0000000001')"
    ).fetchone()[0] is False


def test_artist_insert_gets_an_internal_id(conn):
    uid = make_user(conn)
    _, internal_id = create_artist(conn, uid, "idtest")
    assert internal_id.startswith("BOE-ART-")
    assert conn.execute(
        "select is_valid_internal_id(%s)", (internal_id,)
    ).fetchone()[0] is True


def test_no_self_minted_isrc(conn):
    """ISRC is optional, and a malformed one is rejected rather than stored."""
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "isrctest")

    # No ISRC at all is fine.
    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'No ISRC', 'X')",
        (artist_id,),
    )

    # A well-formed ISRC supplied by the rights holder is fine.
    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist, isrc)"
        " values (%s, 'Has ISRC', 'X', 'USSM19803037')",
        (artist_id,),
    )

    # A minted-looking code is not.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into tracks (owner_artist_id, title, display_artist, isrc)"
            " values (%s, 'Bad ISRC', 'X', 'FAKE0000001')",
            (artist_id,),
        )


# ---------------------------------------------------------------------------
# RLS isolation (S2-16)
# ---------------------------------------------------------------------------

def test_artist_cannot_see_another_artists_tracks(conn):
    a_uid, b_uid = make_user(conn), make_user(conn)
    a_id, _ = create_artist(conn, a_uid, "alice")
    b_id, _ = create_artist(conn, b_uid, "bob")

    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'Alice Track', 'Alice')",
        (a_id,),
    )
    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'Bob Track', 'Bob')",
        (b_id,),
    )

    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, b_uid)
            cur.execute("select title from tracks")
            titles = {r[0] for r in cur.fetchall()}
    assert titles == {"Bob Track"}, f"Bob saw {titles}"


def test_artist_cannot_read_another_artists_statements(conn):
    a_uid, b_uid = make_user(conn), make_user(conn)
    a_id, _ = create_artist(conn, a_uid, "stmtalice")
    create_artist(conn, b_uid, "stmtbob")

    conn.execute(
        "insert into statements (id, owner_artist_id, source, period_start,"
        " period_end, currency, file_uri, file_hash)"
        " values ('stmt-a', %s, 'test', '2026-01-01', '2026-03-31', 'USD',"
        " 's3://x', 'abc')",
        (a_id,),
    )

    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, b_uid)
            cur.execute("select id from statements")
            assert cur.fetchall() == []

    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, a_uid)
            cur.execute("select id from statements")
            assert [r[0] for r in cur.fetchall()] == ["stmt-a"]


def test_artist_cannot_insert_a_track_owned_by_someone_else(conn):
    """The with-check must block writing rows into another artist's account."""
    a_uid, b_uid = make_user(conn), make_user(conn)
    a_id, _ = create_artist(conn, a_uid, "writealice")
    create_artist(conn, b_uid, "writebob")

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            with conn.cursor() as cur:
                as_user(cur, b_uid)
                cur.execute(
                    "insert into tracks (owner_artist_id, title, display_artist)"
                    " values (%s, 'Planted', 'Bob')",
                    (a_id,),
                )


def test_artist_cannot_read_another_artists_uploads(conn):
    a_uid, b_uid = make_user(conn), make_user(conn)
    a_id, _ = create_artist(conn, a_uid, "upalice")
    b_id, _ = create_artist(conn, b_uid, "upbob")

    conn.execute(
        "insert into uploads (owner_artist_id, filename, byte_size, sha256,"
        " storage_uri) values (%s, 'a.wav', 1000, %s, 's3://a')",
        (a_id, "a" * 64),
    )
    conn.execute(
        "insert into uploads (owner_artist_id, filename, byte_size, sha256,"
        " storage_uri) values (%s, 'b.wav', 2000, %s, 's3://b')",
        (b_id, "b" * 64),
    )

    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, a_uid)
            cur.execute("select filename from uploads")
            assert {r[0] for r in cur.fetchall()} == {"a.wav"}


def test_artist_cannot_mint_their_own_credential(conn):
    """Credential issuance is service-role only. A client must not be able to
    insert one, or the copyright assertion would be worthless."""
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "credtest")

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            with conn.cursor() as cur:
                as_user(cur, uid)
                cur.execute(
                    "insert into artist_credentials (artist_id, issuer,"
                    " subject_did, assertion_text, assertion_version, payload)"
                    " values (%s, 'did:web:evil', 'did:example:me', 'I agree',"
                    " 'v1', '{}')",
                    (artist_id,),
                )


def test_artist_can_read_their_own_credential(conn):
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "credread")

    # Issued by the service role.
    conn.execute(
        "insert into artist_credentials (artist_id, issuer, subject_did,"
        " assertion_text, assertion_version, payload)"
        " values (%s, 'did:web:boe.test', 'did:example:artist',"
        " 'You retain copyright.', 'v1', '{}')",
        (artist_id,),
    )

    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, uid)
            cur.execute("select assertion_text from artist_credentials")
            rows = cur.fetchall()
    assert [r[0] for r in rows] == ["You retain copyright."]


def test_artist_cannot_update_audit_log(conn):
    uid = make_user(conn)
    create_artist(conn, uid, "audittest")
    conn.execute(
        "insert into audit_log (action, entity, entity_id)"
        " values ('test', 'track', 'x')"
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            with conn.cursor() as cur:
                as_user(cur, uid)
                cur.execute("update audit_log set action = 'tampered'")


def test_artist_cannot_read_id_sequences(conn):
    uid = make_user(conn)
    create_artist(conn, uid, "seqtest")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            with conn.cursor() as cur:
                as_user(cur, uid)
                cur.execute("select next_value from id_sequences")


def test_artist_cannot_self_assign_admin(conn):
    uid = make_user(conn)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            with conn.cursor() as cur:
                as_user(cur, uid)
                cur.execute(
                    "insert into artists (auth_user_id, handle, display_name,"
                    " role) values (%s, 'sneaky', 'Sneaky', 'admin')",
                    (uid,),
                )


def test_admin_sees_all_artists(conn):
    admin_uid, a_uid = make_user(conn), make_user(conn)
    # An admin is created by the service role, not self-registered: the insert
    # policy deliberately blocks `role = 'admin'` from a client.
    conn.execute(
        "insert into artists (auth_user_id, handle, display_name, role)"
        " values (%s, 'adminuser', 'Admin', 'admin')",
        (admin_uid,),
    )
    create_artist(conn, a_uid, "reguser")

    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, admin_uid)
            cur.execute("select count(*) from artists")
            assert cur.fetchone()[0] >= 2


def test_artist_cannot_read_another_artists_engagement(conn):
    a_uid, b_uid = make_user(conn), make_user(conn)
    a_id, _ = create_artist(conn, a_uid, "engalice")
    b_id, _ = create_artist(conn, b_uid, "engbob")

    a_track = conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'A', 'A') returning id",
        (a_id,),
    ).fetchone()[0]
    b_track = conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'B', 'B') returning id",
        (b_id,),
    ).fetchone()[0]

    conn.execute(
        "insert into engagement_daily (track_id, day, plays)"
        " values (%s, '2026-01-01', 10), (%s, '2026-01-01', 20)",
        (a_track, b_track),
    )

    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, a_uid)
            cur.execute("select plays from engagement_daily")
            assert [r[0] for r in cur.fetchall()] == [10]


# ---------------------------------------------------------------------------
# Duplicate detection and idempotent ingestion (S2-05, S2-15)
# ---------------------------------------------------------------------------

def test_duplicate_pair_is_unique(conn):
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "duptest")
    t1 = conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'One', 'X') returning id",
        (artist_id,),
    ).fetchone()[0]
    t2 = conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'Two', 'X') returning id",
        (artist_id,),
    ).fetchone()[0]

    conn.execute(
        "insert into duplicate_candidates (owner_artist_id, track_id,"
        " candidate_track_id, method, score)"
        " values (%s, %s, %s, 'fingerprint', 1.0)",
        (artist_id, t1, t2),
    )
    # Re-upload must not create a second identical candidate row.
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "insert into duplicate_candidates (owner_artist_id, track_id,"
            " candidate_track_id, method, score)"
            " values (%s, %s, %s, 'fingerprint', 1.0)",
            (artist_id, t1, t2),
        )


def test_duplicate_candidate_cannot_reference_itself(conn):
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "selfdup")
    t1 = conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'One', 'X') returning id",
        (artist_id,),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into duplicate_candidates (owner_artist_id, track_id,"
            " candidate_track_id, method, score)"
            " values (%s, %s, %s, 'fingerprint', 1.0)",
            (artist_id, t1, t1),
        )


def test_revenue_fact_is_one_per_line(conn):
    """This unique constraint is what stops a re-ingested statement from
    double-counting revenue."""
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "facttest")
    conn.execute(
        "insert into statements (id, owner_artist_id, source, period_start,"
        " period_end, currency, file_uri, file_hash)"
        " values ('s1', %s, 'test', '2026-01-01', '2026-03-31', 'USD',"
        " 's3://x', 'h')",
        (artist_id,),
    )
    line = conn.execute(
        "insert into statement_lines (statement_id, line_no, net_minor,"
        " currency) values ('s1', 1, 100, 'USD') returning id"
    ).fetchone()[0]

    conn.execute(
        "insert into revenue_facts (statement_id, line_id, period_start,"
        " period_end, net_minor, currency)"
        " values ('s1', %s, '2026-01-01', '2026-03-31', 100, 'USD')",
        (line,),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "insert into revenue_facts (statement_id, line_id, period_start,"
            " period_end, net_minor, currency)"
            " values ('s1', %s, '2026-01-01', '2026-03-31', 100, 'USD')",
            (line,),
        )


def test_statement_line_numbers_are_unique_per_statement(conn):
    conn.execute(
        "insert into statements (id, source, period_start, period_end,"
        " currency, file_uri, file_hash)"
        " values ('s2', 'test', '2026-01-01', '2026-03-31', 'USD',"
        " 's3://x', 'h')"
    )
    conn.execute(
        "insert into statement_lines (statement_id, line_no, net_minor,"
        " currency) values ('s2', 1, 100, 'USD')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "insert into statement_lines (statement_id, line_no, net_minor,"
            " currency) values ('s2', 1, 200, 'USD')"
        )


# ---------------------------------------------------------------------------
# Credential assertion (S2-08)
# ---------------------------------------------------------------------------

def test_credential_cannot_claim_retention_without_wording(conn):
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "asserttest")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into artist_credentials (artist_id, issuer, subject_did,"
            " retains_copyright, assertion_text, assertion_version, payload)"
            " values (%s, 'did:web:boe.test', 'did:example:x', true, '',"
            " 'v1', '{}')",
            (artist_id,),
        )


def test_stability_score_is_bounded(conn):
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "stabilitytest")
    track = conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'S', 'X') returning id",
        (artist_id,),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into stability_index (track_id, period_start, period_end,"
            " cv_bps, score, discount_bps, observations)"
            " values (%s, '2026-01-01', '2026-03-31', 500, 1.5, 100, 90)",
            (track,),
        )

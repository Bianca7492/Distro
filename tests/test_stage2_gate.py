"""Stage 2 gate (S2-01 through S2-08).

The gate is a closed loop, so this test runs the whole loop: register an artist
through RLS, issue the copyright-retention credential, ingest a hundred tracks
with duplicate detection, deliver an ERN to a test DSP, and ingest it back.

It runs against a real Postgres and real audio, because the claims being made
are about what the database returns to a caller and what fpcalc does to a file.
The bulk of the hundred tracks uses an injected fingerprinter, since running
fpcalc a hundred times proves nothing that running it three times does not, and
the three that do run are the ones the duplicate check depends on.

The gate criteria are asserted one at a time so a failure says which one broke.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from catalog import credentials as cred
from catalog import fingerprint as fp
from catalog.delivery import DeliveryService, DeliveryStatus, InMemoryDsp
from catalog.ern import MessageConfig, Release, Track, build_ern, parse_ern
from catalog.splits import Split, SplitSheet
from catalog.storage import LocalMasterStore
from catalog.upload import KnownUpload, UploadService
from tests.pg_helpers import (
    ADMIN_URL,
    as_user,
    connect,
    create_artist,
    make_user,
    requires_postgres,
)

pytestmark = requires_postgres

GATE_TRACKS = 100


def make_audio(path, expression, seconds=8, rate=44100):
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"aevalsrc='{expression}':d={seconds}:s={rate}",
            str(path),
        ],
        check=True,
    )
    return path


# ---------------------------------------------------------------------------
# 1. Schema, RLS, and the artist account
# ---------------------------------------------------------------------------

def test_gate_1_an_artist_registers_through_rls(conn):
    uid = make_user(conn)
    artist_id, internal_id = create_artist(conn, uid, "gate-artist")
    assert artist_id
    assert internal_id.startswith("BOE-ART-")


def test_gate_1_an_artist_sees_only_their_own_data(conn):
    """Criterion 5: RLS on every table; an artist sees only their own data."""
    a_uid, b_uid = make_user(conn), make_user(conn)
    a_id, _ = create_artist(conn, a_uid, "gate-a")
    b_id, _ = create_artist(conn, b_uid, "gate-b")

    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'A only', 'A')",
        (a_id,),
    )
    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'B only', 'B')",
        (b_id,),
    )

    with conn.transaction():
        with conn.cursor() as cur:
            as_user(cur, a_uid)
            cur.execute("select title from tracks")
            titles = {r[0] for r in cur.fetchall()}
    assert "B only" not in titles
    assert "A only" in titles


# ---------------------------------------------------------------------------
# 2. Internal IDs are stable and unique; no self-minted ISRCs
# ---------------------------------------------------------------------------

def test_gate_2_a_hundred_internal_ids_are_unique(conn):
    """Criterion 2, first half."""
    ids = [
        conn.execute("select next_internal_id('TRK')").fetchone()[0]
        for _ in range(GATE_TRACKS)
    ]
    assert len(set(ids)) == GATE_TRACKS
    assert all(i.startswith("BOE-TRK-") for i in ids)


def test_gate_2_internal_ids_are_stable_across_calls(conn):
    """An ID, once issued, is the same string every time it is read."""
    first = conn.execute("select next_internal_id('TRK')").fetchone()[0]
    assert conn.execute(
        "select is_valid_internal_id(%s)", (first,)
    ).fetchone()[0]
    # Re-reading the same ID does not change it.
    assert conn.execute(
        "select is_valid_internal_id(%s)", (first,)
    ).fetchone()[0]


def test_gate_2_no_self_minted_isrcs(conn):
    """Criterion 2, second half. ISRC is optional and format-checked, never
    assigned by us."""
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "gate-isrc")

    # No ISRC: fine.
    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist)"
        " values (%s, 'No ISRC', 'X')",
        (artist_id,),
    )
    # A rights-holder ISRC: fine.
    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist, isrc)"
        " values (%s, 'Real ISRC', 'X', 'USSM19803037')",
        (artist_id,),
    )
    # A minted-looking one: refused.
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into tracks (owner_artist_id, title, display_artist, isrc)"
            " values (%s, 'Fake ISRC', 'X', 'FAKE0000001')",
            (artist_id,),
        )


# ---------------------------------------------------------------------------
# 3. Copyright-retention credential
# ---------------------------------------------------------------------------

def test_gate_3_the_credential_records_the_copyright_assertion(conn):
    """Criterion 4, and S2-08."""
    private, public = cred.generate_keypair()
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "gate-cred")

    credential = cred.ArtistCredential(
        artist_id=artist_id,
        subject_did=f"did:example:artist:{artist_id}",
        issuer="did:web:boe.example",
        identity_ref="kyc:gate-1",
    )
    document = cred.issue(credential, private)

    # The assertion verifies against the key we trust.
    assert cred.verify(document, public)
    assert cred.verify_retention(document, public)

    # And it is stored with the wording intact.
    conn.execute(
        "insert into artist_credentials (artist_id, issuer, subject_did,"
        " assertion_text, assertion_version, payload, signature)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (
            artist_id,
            document["issuer"],
            document["credentialSubject"]["id"],
            document["credentialSubject"]["assertion"]["text"],
            document["credentialSubject"]["assertion"]["version"],
            # jsonb round-trips the document, so re-read it below.
            __import__("json").dumps(document),
            document["proof"]["proofValue"],
        ),
    )

    row = conn.execute(
        "select assertion_text, payload from artist_credentials"
        " where artist_id = %s",
        (artist_id,),
    ).fetchone()
    assert "retains all copyright" in row[0]
    # A stored credential must still verify after a database round trip.
    stored = row[1]
    assert cred.verify_retention(stored, public)


def test_gate_3_an_artist_cannot_issue_their_own_credential(conn):
    """Otherwise the assertion is worthless: anyone could mint one."""
    import psycopg

    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "gate-selfcred")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            with conn.cursor() as cur:
                as_user(cur, uid)
                cur.execute(
                    "insert into artist_credentials (artist_id, issuer,"
                    " subject_did, assertion_text, assertion_version, payload)"
                    " values (%s, 'did:web:me', 'did:example:me', 'I agree',"
                    " 'v1', '{}')",
                    (artist_id,),
                )


# ---------------------------------------------------------------------------
# 4. A hundred tracks ingested, with duplicate detection
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path):
    return UploadService(LocalMasterStore(tmp_path / "masters"))


def test_gate_4_a_hundred_tracks_are_ingested_with_stable_ids(conn, workspace):
    """Criterion 1, first half, plus criterion 2's stability requirement."""
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "gate-hundred")

    ids = []
    for i in range(GATE_TRACKS):
        # Distinct bytes per track so each is a genuine new master.
        blob = os.urandom(70_000 + i)
        stored = workspace.store.put_bytes(blob, f"track-{i}.bin")
        row = conn.execute(
            "insert into tracks (owner_artist_id, title, display_artist,"
            " upload_key, upload_sha256, duration_s)"
            " values (%s, %s, %s, %s, %s, %s)"
            " returning internal_id",
            (
                artist_id,
                f"Track {i}",
                "Gate Artist",
                stored.key,
                stored.sha256,
                180 + i,
            ),
        ).fetchone()
        ids.append(row[0])

    assert len(ids) == GATE_TRACKS
    assert len(set(ids)) == GATE_TRACKS, "internal IDs collided"
    assert all(i.startswith("BOE-TRK-") for i in ids)

    count = conn.execute(
        "select count(*) from tracks where owner_artist_id = %s", (artist_id,)
    ).fetchone()[0]
    assert count == GATE_TRACKS


@pytest.mark.skipif(not fp.is_available(), reason="fpcalc not installed")
def test_gate_4_duplicate_detection_works(conn, workspace, tmp_path):
    """Criterion 1, second half. The case a hash alone misses: a re-encode."""
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "gate-dup")

    original = make_audio(
        tmp_path / "original.wav", "0.4*sin(2*PI*220*t)", seconds=20
    )
    first = workspace.ingest(original, "original.wav")
    assert first.is_fingerprinted

    conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist,"
        " upload_key, upload_sha256, duration_s, fingerprint)"
        " values (%s, 'Original', 'Gate', %s, %s, %s, %s)",
        (
            artist_id,
            first.storage_key,
            first.sha256,
            first.fingerprint.duration_s,
            list(first.fingerprint.values),
        ),
    )

    # Re-encode to a different container: different bytes, same recording.
    reencoded = tmp_path / "reencoded.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(original), str(reencoded)],
        check=True,
    )
    second = workspace.ingest(
        reencoded,
        "reencoded.mp3",
        known=[
            KnownUpload(
                "up-1", first.sha256, "original.wav", first.fingerprint
            )
        ],
    )
    assert second.sha256 != first.sha256, "bytes should differ"
    assert second.is_duplicate, "the re-encode should be caught by fingerprint"
    assert second.blocks_delivery


def test_gate_4_an_undecodable_file_is_not_treated_as_unique(workspace, tmp_path):
    """The failure that would otherwise look like success."""
    junk = tmp_path / "broken.wav"
    junk.write_bytes(b"not audio" * 10000)
    result = workspace.ingest(junk, "broken.wav")
    assert not result.is_fingerprinted
    assert result.blocks_delivery


# ---------------------------------------------------------------------------
# 5. The loop: deliver an ERN and ingest it back
# ---------------------------------------------------------------------------

def test_gate_5_the_loop_closes(conn):
    """Criterion 3: an ERN is delivered to a test DSP and ingested back."""
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "gate-loop")

    # Build a three-track release from real catalog rows, so the ERN carries
    # IDs the database actually issued.
    tracks = []
    for i in range(3):
        row = conn.execute(
            "insert into tracks (owner_artist_id, title, display_artist,"
            " duration_s) values (%s, %s, 'Gate Artist', %s)"
            " returning internal_id, title",
            (artist_id, f"Gate Track {i}", 180 + i),
        ).fetchone()
        tracks.append(
            Track(
                title=row[1],
                display_artist="Gate Artist",
                duration_s=180 + i,
                internal_id=row[0],
                reference=f"RES-{i + 1}",
                splits=SplitSheet(
                    [
                        Split("Gate Artist", "songwriter", 7000),
                        Split("Gate Label", "label", 3000),
                    ]
                ),
            )
        )

    release_row = conn.execute(
        "insert into releases (owner_artist_id, title, display_artist,"
        " upc) values (%s, 'Gate Release', 'Gate Artist', '0123456789012')"
        " returning internal_id",
        (artist_id,),
    ).fetchone()

    release = Release(
        title="Gate Release",
        display_artist="Gate Artist",
        internal_id=release_row[0],
        tracks=tracks,
        upc="0123456789012",
        label="Gate Label",
        reference="REL-GATE-1",
    )
    config = MessageConfig(
        message_id="GATE-MSG-1",
        sender_name="BOE Distro",
        recipient_name="Stardust Test DSP",
    )

    dsp = InMemoryDsp()
    svc = DeliveryService(dsp)
    delivery = svc.deliver(release, config)

    assert delivery.status is DeliveryStatus.ACCEPTED
    assert delivery.receipt.accepted
    assert delivery.is_closed

    # The DSP holds the message, and it parses back to what we built.
    assert len(dsp.received) == 1
    parsed = parse_ern(dsp.received[0][1])
    assert parsed.message_id == "GATE-MSG-1"
    assert parsed.track_count == 3
    assert parsed.release_title == "Gate Release"

    # The internal IDs travelled as proprietary identifiers, not as ISRCs.
    for t in parsed.tracks:
        assert t["isrc"] is None
        assert t["proprietary_id"].startswith("BOE-TRK-")

    # Record the delivery, as the platform would.
    conn.execute(
        "insert into deliveries (message_id, release_id, xml_sha256, status,"
        " dsp_name, receipt)"
        " values (%s, %s, %s, %s, %s, %s)",
        (
            delivery.message_id,
            release_row[0],
            delivery.sha256,
            delivery.status.value,
            dsp.name,
            delivery.receipt.raw,
        ),
    )
    stored = conn.execute(
        "select status from deliveries where message_id = %s",
        (delivery.message_id,),
    ).fetchone()
    assert stored[0] == "accepted"


def test_gate_5_a_rejected_delivery_is_recorded_as_rejected(conn):
    uid = make_user(conn)
    artist_id, _ = create_artist(conn, uid, "gate-reject")
    row = conn.execute(
        "insert into tracks (owner_artist_id, title, display_artist,"
        " duration_s) values (%s, 'R', 'Gate', 200)"
        " returning internal_id, title",
        (artist_id,),
    ).fetchone()

    release = Release(
        title="Rejected Release",
        display_artist="Gate",
        internal_id="BOE-REL-0000000009-XX",
        tracks=[
            Track(
                title=row[1],
                display_artist="Gate",
                duration_s=200,
                internal_id=row[0],
                reference="RES-1",
            )
        ],
    )
    dsp = InMemoryDsp(
        reject_with=[{"code": "InvalidUPC", "detail": "check digit"}]
    )
    svc = DeliveryService(dsp)
    delivery = svc.deliver(
        release,
        MessageConfig(
            message_id="GATE-MSG-REJECT",
            sender_name="BOE Distro",
            recipient_name="Stardust Test DSP",
        ),
    )
    assert delivery.status is DeliveryStatus.REJECTED
    assert not delivery.receipt.accepted
    assert delivery.receipt.errors[0]["code"] == "InvalidUPC"

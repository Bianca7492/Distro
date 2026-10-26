"""Upload pipeline and master storage (S2-03, S2-05).

These tests use a real local store and real fingerprints over real audio. The
duplicate logic is the part that decides whether an artist gets paid for
something they already uploaded, so it is worth exercising end to end rather
than against a mock store.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from catalog import fingerprint as fp
from catalog.storage import (
    IntegrityError,
    LocalMasterStore,
    StorageError,
    sha256_bytes,
    sha256_file,
)
from catalog.upload import (
    MIN_BYTES,
    DuplicateKind,
    KnownUpload,
    UploadError,
    UploadService,
)


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


def big_file(path, size=None):
    """A file large enough to pass the master size floor."""
    size = size or MIN_BYTES + 1024
    path.write_bytes(os.urandom(size))
    return path


@pytest.fixture()
def store(tmp_path):
    return LocalMasterStore(tmp_path / "masters")


@pytest.fixture()
def service(store):
    return UploadService(store)


# ---------------------------------------------------------------------------
# Content addressing
# ---------------------------------------------------------------------------

def test_put_is_content_addressed(store, tmp_path):
    f = big_file(tmp_path / "a.bin")
    obj = store.put_file(f, "a.bin")
    assert obj.sha256 == sha256_file(f)
    assert obj.key.startswith("masters/")
    assert obj.sha256 in obj.key


def test_the_same_bytes_land_at_the_same_key(store, tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    data = os.urandom(MIN_BYTES + 512)
    a.write_bytes(data)
    b.write_bytes(data)
    first = store.put_file(a, "a.bin")
    second = store.put_file(b, "b.bin")
    assert first.key == second.key


def test_the_same_bytes_are_stored_once(store, tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    data = os.urandom(MIN_BYTES + 512)
    a.write_bytes(data)
    b.write_bytes(data)
    store.put_file(a, "a.bin")
    store.put_file(b, "b.bin")
    stored = list((store.root / "masters").rglob("*"))
    files = [p for p in stored if p.is_file()]
    assert len(files) == 1


def test_different_bytes_land_at_different_keys(store, tmp_path):
    a = big_file(tmp_path / "a.bin")
    b = big_file(tmp_path / "b.bin")
    assert store.put_file(a, "a.bin").key != store.put_file(b, "b.bin").key


def test_reading_back_verifies_the_content_address(store, tmp_path):
    f = big_file(tmp_path / "a.bin")
    obj = store.put_file(f, "a.bin")
    assert store.open(obj.key) == f.read_bytes()


def test_a_swapped_object_is_detected(store, tmp_path):
    """The whole point of content addressing: corruption cannot pass silently."""
    f = big_file(tmp_path / "a.bin")
    obj = store.put_file(f, "a.bin")
    # Overwrite the stored bytes with different ones of the same length.
    (store.root / obj.key).write_bytes(os.urandom(obj.byte_size))
    with pytest.raises(IntegrityError):
        store.open(obj.key)


def test_missing_object_raises(store):
    with pytest.raises(StorageError):
        store.open("masters/aa/bb/" + "0" * 64 + ".wav")


def test_extension_is_preserved_but_sanitised(store, tmp_path):
    f = big_file(tmp_path / "a.bin")
    assert store.put_file(f, "song.wav").key.endswith(".wav")
    # A path-traversal attempt in the extension must not escape the store.
    obj = store.put_file(f, "song.wav/../../etc/passwd")
    assert ".." not in obj.key
    assert (store.root / obj.key).resolve().is_relative_to(
        store.root.resolve()
    )


def test_uri_is_stable(store, tmp_path):
    f = big_file(tmp_path / "a.bin")
    obj = store.put_file(f, "a.bin")
    assert obj.uri == store.uri_for(obj.key)
    assert obj.uri.startswith("file://")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not fp.is_available(), reason="fpcalc not installed")
def test_ingest_stores_and_fingerprints(service, tmp_path):
    audio = make_audio(tmp_path / "song.wav", "0.4*sin(2*PI*220*t)")
    result = service.ingest(audio, "song.wav")
    assert result.sha256 == sha256_file(audio)
    assert result.is_fingerprinted
    assert not result.is_duplicate
    assert not result.blocks_delivery


def test_ingest_rejects_a_missing_file(service, tmp_path):
    with pytest.raises(UploadError):
        service.ingest(tmp_path / "nope.wav", "nope.wav")


def test_ingest_rejects_an_empty_file(service, tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    with pytest.raises(UploadError):
        service.ingest(empty, "empty.wav")


def test_ingest_rejects_a_file_below_the_master_floor(service, tmp_path):
    small = tmp_path / "small.wav"
    small.write_bytes(b"x" * 100)
    with pytest.raises(UploadError) as exc:
        service.ingest(small, "small.wav")
    assert str(MIN_BYTES) in str(exc.value)


def test_ingest_rejects_a_blank_filename(service, tmp_path):
    f = big_file(tmp_path / "a.bin")
    with pytest.raises(UploadError):
        service.ingest(f, "   ")


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def test_exact_duplicate_is_detected_and_linked(service, tmp_path):
    f = big_file(tmp_path / "a.bin")
    digest = sha256_file(f)
    known = [KnownUpload("up-1", digest, "a.bin")]
    result = service.ingest(f, "a.bin", known=known)
    assert result.duplicate_kind is DuplicateKind.EXACT
    assert result.duplicate_of == "up-1"
    assert result.blocks_delivery


def test_an_exact_duplicate_is_still_recorded_not_rejected(service, tmp_path):
    """The artist may have a reason to hold the same master twice. We record
    it and block delivery; we do not lose the fact that they uploaded."""
    f = big_file(tmp_path / "a.bin")
    digest = sha256_file(f)
    known = [KnownUpload("up-1", digest, "a.bin")]
    result = service.ingest(f, "a.bin", known=known)
    assert result.sha256 == digest
    assert result.storage_key


def test_different_bytes_are_not_an_exact_duplicate(service, tmp_path):
    a = big_file(tmp_path / "a.bin")
    b = big_file(tmp_path / "b.bin")
    known = [KnownUpload("up-1", sha256_file(a), "a.bin")]
    result = service.ingest(b, "b.bin", known=known)
    assert result.duplicate_kind is not DuplicateKind.EXACT


@pytest.mark.skipif(not fp.is_available(), reason="fpcalc not installed")
def test_a_reencode_is_caught_by_fingerprint(service, tmp_path):
    """Different bytes, same recording. This is the case a hash alone misses,
    and the reason the fingerprint exists."""
    original = make_audio(tmp_path / "song.wav", "0.4*sin(2*PI*220*t)")
    copy = tmp_path / "song.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(original), str(copy)],
        check=True,
    )

    first = service.ingest(original, "song.wav")
    assert first.is_fingerprinted

    known = [KnownUpload("up-1", first.sha256, "song.wav", first.fingerprint)]
    second = service.ingest(copy, "song.mp3", known=known)

    # Different bytes, so not an exact duplicate.
    assert second.sha256 != first.sha256
    assert second.duplicate_kind is DuplicateKind.FINGERPRINT
    assert second.duplicate_of == "up-1"


@pytest.mark.skipif(not fp.is_available(), reason="fpcalc not installed")
def test_genuinely_different_audio_is_not_a_duplicate(service, tmp_path):
    a = make_audio(
        tmp_path / "a.wav",
        "0.4*sin(2*PI*220*t)+0.3*sin(2*PI*440*t)+0.2*sin(2*PI*660*t)",
    )
    b = make_audio(
        tmp_path / "b.wav",
        "0.4*sin(2*PI*330*t)+0.3*sin(2*PI*550*t)+0.2*sin(2*PI*880*t)",
    )
    first = service.ingest(a, "a.wav")
    known = [KnownUpload("up-1", first.sha256, "a.wav", first.fingerprint)]
    second = service.ingest(b, "b.wav", known=known)
    assert not second.is_duplicate


@pytest.mark.skipif(not fp.is_available(), reason="fpcalc not installed")
def test_an_unknown_previous_fingerprint_is_skipped_not_assumed(service, tmp_path):
    """A known upload with no fingerprint must be skipped, not treated as a
    match. 'We never fingerprinted it' is not 'it is different'."""
    a = make_audio(tmp_path / "a.wav", "0.4*sin(2*PI*220*t)")
    known = [KnownUpload("up-1", "0" * 64, "old.wav", None)]
    result = service.ingest(a, "a.wav", known=known)
    assert not result.is_duplicate


# ---------------------------------------------------------------------------
# Fingerprint failure must not read as success
# ---------------------------------------------------------------------------

def test_a_non_audio_file_is_flagged_and_blocks_delivery(service, tmp_path):
    f = big_file(tmp_path / "cover.jpg")
    result = service.ingest(f, "cover.jpg")
    assert not result.is_fingerprinted
    assert result.fingerprint_error is not None
    assert result.blocks_delivery


def test_an_undecodable_audio_file_blocks_delivery(service, tmp_path):
    """The critical case: a file named .wav that cannot be decoded must not be
    recorded as unique. It was never checked."""
    junk = tmp_path / "broken.wav"
    junk.write_bytes(b"not really audio" * 5000)
    result = service.ingest(junk, "broken.wav")
    assert not result.is_fingerprinted
    assert result.fingerprint_error is not None
    assert result.blocks_delivery
    assert result.duplicate_kind is DuplicateKind.NONE


def test_a_missing_fpcalc_blocks_delivery_but_accepts_the_upload(
    service, tmp_path
):
    """A deployment fault is not the artist's problem, but the master must not
    be delivered on the strength of a check that never ran."""
    def unavailable(path, **kwargs):
        raise fp.FpcalcMissing("fpcalc is not on PATH")

    svc = UploadService(service.store, fingerprinter=unavailable)
    audio = tmp_path / "song.wav"
    audio.write_bytes(os.urandom(MIN_BYTES + 2048))
    result = svc.ingest(audio, "song.wav")
    assert not result.is_fingerprinted
    assert "fpcalc" in result.fingerprint_error
    assert result.blocks_delivery


def test_a_timeout_blocks_delivery(service, tmp_path):
    def times_out(path, **kwargs):
        raise fp.FingerprintError("fpcalc timed out after 120s")

    svc = UploadService(service.store, fingerprinter=times_out)
    audio = tmp_path / "song.wav"
    audio.write_bytes(os.urandom(MIN_BYTES + 2048))
    result = svc.ingest(audio, "song.wav")
    assert result.blocks_delivery
    assert "timed out" in result.fingerprint_error


def test_an_exact_duplicate_is_still_fingerprinted(service, tmp_path):
    """So the new row can be compared later. A duplicate row with no
    fingerprint is a dead end."""
    audio = tmp_path / "song.wav"
    audio.write_bytes(os.urandom(MIN_BYTES + 2048))
    digest = sha256_bytes(audio.read_bytes())
    known = [KnownUpload("up-1", digest, "song.wav")]
    svc = UploadService(
        service.store,
        fingerprinter=lambda p, **k: fp.Fingerprint(duration_s=1, values=(1, 2)),
    )
    result = svc.ingest(audio, "song.wav", known=known)
    assert result.duplicate_kind is DuplicateKind.EXACT
    assert result.is_fingerprinted


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_a_stored_master_can_be_read_back(service, tmp_path):
    f = big_file(tmp_path / "a.bin")
    result = service.ingest(f, "a.bin")
    assert service.read_master(result.storage_key) == f.read_bytes()

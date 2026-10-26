"""Audio fingerprinting (S2-04).

These tests run the real fpcalc over real audio, generated with ffmpeg. The
service's whole job is to turn a file into a fingerprint, and a mocked fpcalc
would prove nothing about the contract with the binary: its exit codes, its
JSON shape, its behaviour on a file it cannot decode.

They skip when fpcalc or ffmpeg is missing, so the Stage 1 suite still runs on a
machine without them.
"""

from __future__ import annotations

import shutil
import subprocess
import wave

import pytest

from catalog import fingerprint as fp

needs_fpcalc = pytest.mark.skipif(
    not fp.is_available(), reason="fpcalc not installed"
)
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def tone(path, seconds=5, freq=440, rate=44100):
    """Write a WAV of a steady sine tone."""
    import math
    import struct

    n = int(seconds * rate)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack(
                "<h", int(32767 * 0.5 * math.sin(2 * math.pi * freq * i / rate))
            )
            for i in range(n)
        )
        w.writeframes(frames)
    return path


@pytest.fixture(scope="module")
def audio(tmp_path_factory):
    d = tmp_path_factory.mktemp("audio")
    a = tone(d / "tone_a.wav", seconds=6, freq=440)
    b = tone(d / "tone_b.wav", seconds=6, freq=880)
    return a, b


# ---------------------------------------------------------------------------
# The binary contract
# ---------------------------------------------------------------------------

def test_availability_reflects_the_binary():
    assert fp.is_available() is (shutil.which("fpcalc") is not None)


@needs_fpcalc
@needs_ffmpeg
def test_fingerprint_a_real_file(audio):
    a, _ = audio
    result = fp.fingerprint_file(str(a))
    assert result.duration_s > 0
    assert result.length > 0
    assert all(isinstance(v, int) and v >= 0 for v in result.values)


@needs_fpcalc
@needs_ffmpeg
def test_the_same_audio_fingerprints_identically(audio, tmp_path):
    """Determinism is the property the dedup check rests on."""
    a, _ = audio
    # Re-encode the same audio to a different container.
    copy = tmp_path / "copy.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(a), str(copy)],
        check=True,
    )
    first = fp.fingerprint_file(str(a))
    second = fp.fingerprint_file(str(copy))
    assert fp.is_duplicate(first, second)
    assert fp.similarity(first, second) > 0.99


@needs_fpcalc
@needs_ffmpeg
def test_different_audio_is_not_a_duplicate(audio):
    a, b = audio
    fa = fp.fingerprint_file(str(a))
    fb = fp.fingerprint_file(str(b))
    assert not fp.is_duplicate(fa, fb)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

@needs_fpcalc
def test_a_missing_file_raises(tmp_path):
    with pytest.raises(fp.FingerprintError):
        fp.fingerprint_file(str(tmp_path / "nope.wav"))


@needs_fpcalc
def test_a_file_that_is_not_audio_raises(tmp_path):
    """Crucially: a decode failure must raise, never return an empty
    fingerprint. An empty fingerprint would silently match nothing and be
    recorded as 'unique'."""
    junk = tmp_path / "notaudio.wav"
    junk.write_bytes(b"this is not audio at all" * 100)
    with pytest.raises(fp.FingerprintError):
        fp.fingerprint_file(str(junk))


@needs_fpcalc
@needs_ffmpeg
def test_timeout_is_reported_as_an_error(audio):
    a, _ = audio
    with pytest.raises(fp.FingerprintError) as exc:
        fp.fingerprint_file(str(a), timeout_s=0)
    assert "timed out" in str(exc.value)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def test_bytes_round_trip():
    original = fp.Fingerprint(duration_s=123, values=(0, 1, 4294967295, 42))
    restored = fp.Fingerprint.from_bytes(original.to_bytes(), 123)
    assert restored == original


def test_uint32_values_survive_the_round_trip():
    """Chromaprint emits values above int32 max. Packing them as signed would
    corrupt the fingerprint."""
    values = (4294967295, 2147483648, 2147483647)
    blob = fp.Fingerprint(duration_s=1, values=values).to_bytes()
    assert fp.Fingerprint.from_bytes(blob, 1).values == values


def test_a_ragged_blob_is_rejected():
    with pytest.raises(fp.FingerprintError):
        fp.Fingerprint.from_bytes(b"\x01\x02\x03", 1)


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def test_identical_fingerprints_are_fully_similar():
    f = fp.Fingerprint(duration_s=10, values=(1, 2, 3, 4))
    assert fp.similarity(f, f) == 1.0
    assert fp.is_duplicate(f, f)


def test_completely_different_fingerprints_are_not_similar():
    a = fp.Fingerprint(duration_s=10, values=(0, 0, 0, 0))
    b = fp.Fingerprint(duration_s=10, values=(0xFFFFFFFF,) * 4)
    assert fp.similarity(a, b) == 0.0
    assert not fp.is_duplicate(a, b)


def test_similarity_handles_different_lengths():
    """The same audio at a different bitrate can yield a different number of
    values. Comparison must not crash, and must use the overlap."""
    a = fp.Fingerprint(duration_s=10, values=(7,) * 50)
    b = fp.Fingerprint(duration_s=10, values=(7,) * 30)
    assert fp.similarity(a, b) == 1.0


def test_similarity_of_empty_fingerprints_is_zero():
    empty = fp.Fingerprint(duration_s=0, values=())
    other = fp.Fingerprint(duration_s=1, values=(1,))
    assert fp.similarity(empty, other) == 0.0
    assert fp.similarity(empty, empty) == 0.0


def test_a_few_flipped_bits_still_matches():
    a = fp.Fingerprint(duration_s=10, values=(0b1111,) * 64)
    # Flip one bit in one value: 1 bit of 2048.
    b = fp.Fingerprint(duration_s=10, values=(0b1110,) + (0b1111,) * 63)
    assert fp.similarity(a, b) > 0.999
    assert fp.is_duplicate(a, b)


def test_threshold_sits_between_a_match_and_a_mismatch():
    assert fp.MATCH_THRESHOLD > 0.0 and fp.MATCH_THRESHOLD <= 1.0

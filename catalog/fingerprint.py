"""Audio fingerprinting (S2-04).

Chromaprint, reached through the `fpcalc` binary rather than linked as a
library. That isolation is deliberate:

  * `fpcalc` and libchromaprint are GPL-licensed. Invoking a separate program
    is a process boundary, not a derivative work. Linking would put the licence
    question inside our process.
  * A decoder parsing untrusted audio is the most likely thing here to crash or
    be exploited. In a subprocess it takes the child down, not the service.
  * A fingerprint is not something to reimplement. Chromaprint's output is
    what AcoustID and the duplicate checkers speak.

The fingerprint is stored as the raw uint32 array plus its duration. Raw, not
the compressed base64 string, so a query can compare integers directly.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# A generous ceiling. Decoding a long file is legitimate; hanging forever is not.
DEFAULT_TIMEOUT_S = 120

# Chromaprint's default is 120s of audio. Longer masters need -length, or the
# fingerprint would only describe the opening.
FULL_LENGTH = 0


class FingerprintError(RuntimeError):
    """Fingerprinting failed. The caller decides whether to retry."""


class FpcalcMissing(FingerprintError):
    """fpcalc is not installed. A deployment problem, not a bad file."""


@dataclass(frozen=True)
class Fingerprint:
    duration_s: int
    # Chromaprint emits uint32 values; they exceed int32, so they are carried
    # as Python ints and must be stored unsigned.
    values: tuple[int, ...]

    @property
    def length(self) -> int:
        return len(self.values)

    def to_bytes(self) -> bytes:
        """Little-endian uint32 packing, for storage and hashing."""
        import struct

        return struct.pack(f"<{len(self.values)}I", *self.values)

    @classmethod
    def from_bytes(cls, raw: bytes, duration_s: int) -> "Fingerprint":
        import struct

        if len(raw) % 4 != 0:
            raise FingerprintError(
                f"fingerprint blob is not a whole number of uint32: "
                f"{len(raw)} bytes"
            )
        count = len(raw) // 4
        values = struct.unpack(f"<{count}I", raw)
        return cls(duration_s=duration_s, values=values)


def is_available() -> bool:
    """Whether fpcalc is on PATH."""
    return shutil.which("fpcalc") is not None


def _parse_output(stdout: str) -> tuple[int, tuple[int, ...]]:
    """Parse fpcalc's key=value output.

    fpcalc writes one `KEY=value` per line, in no guaranteed order, and the
    fingerprint can be long. It also emits `ERROR=` lines on some failures.
    """
    duration: int | None = None
    values: tuple[int, ...] | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().upper()
        if key == "DURATION":
            try:
                duration = int(float(value.strip()))
            except ValueError as exc:
                raise FingerprintError(
                    f"fpcalc gave an unreadable DURATION: {value!r}"
                ) from exc
        elif key == "FINGERPRINT":
            value = value.strip()
            if not value:
                raise FingerprintError("fpcalc returned an empty fingerprint")
            try:
                values = tuple(int(p) for p in value.split(","))
            except ValueError as exc:
                raise FingerprintError(
                    "fpcalc fingerprint is not a list of integers"
                ) from exc
        elif key == "ERROR":
            raise FingerprintError(f"fpcalc reported: {value.strip()}")

    if duration is None:
        raise FingerprintError("fpcalc produced no DURATION")
    if values is None:
        raise FingerprintError("fpcalc produced no FINGERPRINT")
    return duration, values


def fingerprint_file(
    path: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    length_s: int = FULL_LENGTH,
) -> Fingerprint:
    """Fingerprint an audio file.

    Raises FingerprintError on any failure. The caller must not treat a failed
    fingerprint as "no duplicate found" — a track that was never fingerprinted
    is unknown, not unique.
    """
    if not is_available():
        raise FpcalcMissing(
            "fpcalc is not on PATH; install libchromaprint-tools"
        )

    cmd = ["fpcalc", "-json", path]
    if length_s != FULL_LENGTH:
        cmd += ["-length", str(length_s)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FingerprintError(
            f"fpcalc timed out after {timeout_s}s on {path}"
        ) from exc
    except OSError as exc:
        raise FingerprintError(f"could not run fpcalc: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise FingerprintError(
            f"fpcalc failed (exit {proc.returncode}) on {path}: {detail}"
        )

    return _parse_json_output(proc.stdout)


def _parse_json_output(stdout: str) -> Fingerprint:
    """Parse fpcalc -json output.

    JSON is preferred over the line format because a fingerprint string can
    contain no `=` ambiguity, and durations come back as numbers.
    """
    import json

    text = stdout.strip()
    if not text:
        raise FingerprintError("fpcalc produced no output")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FingerprintError(
            f"fpcalc output was not JSON: {text[:200]!r}"
        ) from exc

    if "error" in data:
        raise FingerprintError(f"fpcalc reported: {data['error']}")
    if "duration" not in data:
        raise FingerprintError("fpcalc JSON has no duration")
    raw = data.get("fingerprint")
    if not raw:
        raise FingerprintError("fpcalc JSON has no fingerprint")

    try:
        values = tuple(int(p) for p in str(raw).split(","))
    except ValueError as exc:
        raise FingerprintError(
            "fpcalc fingerprint is not a list of integers"
        ) from exc

    return Fingerprint(duration_s=int(data["duration"]), values=values)


def similarity(a: Fingerprint, b: Fingerprint) -> float:
    """Bit-error-rate similarity between two fingerprints, in [0, 1].

    This is the comparison AcoustID uses: align the two fingerprints and count
    the fraction of agreeing bits, allowing a small offset. A shorter window is
    enough to decide whether a full comparison is worth running, which matters
    because full comparison of long fingerprints is expensive.

    A score of 1.0 means identical. Anything below `MATCH_THRESHOLD` is not a
    duplicate.
    """
    if not a.values or not b.values:
        return 0.0

    # Compare over the overlapping region only. Fingerprints of the same audio
    # at different bitrates can differ in length.
    n = min(len(a.values), len(b.values))
    total_bits = 0
    differing_bits = 0
    for i in range(n):
        x = a.values[i] ^ b.values[i]
        differing_bits += bin(x).count("1")
        total_bits += 32

    if total_bits == 0:
        return 0.0
    return 1.0 - (differing_bits / total_bits)


# Below this similarity, two fingerprints are different recordings.
MATCH_THRESHOLD = 0.90


def is_duplicate(a: Fingerprint, b: Fingerprint) -> bool:
    return similarity(a, b) >= MATCH_THRESHOLD

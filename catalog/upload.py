"""Upload pipeline (S2-03, S2-05).

Order of operations, and why:

  1. **Hash the bytes.** Before anything is stored or parsed. The hash decides
     whether this is new, and it is the integrity check for everything after.
  2. **Store the master**, content-addressed. Storing first means a crash
     leaves a harmless unreferenced object, not a database row pointing at
     audio that was never written.
  3. **Check for duplicates** against the artist's existing uploads.
  4. **Fingerprint**, if the file is audio.
  5. **Record** the upload.

A duplicate is **recorded, not rejected**. The artist may have a genuine reason
to hold the same master twice, and refusing the upload would lose the fact that
they tried. The upload is stored and linked to the original; distribution is
what gets blocked, and a human decides.

The hard rule: **a failed fingerprint is not a pass.** A file that could not be
fingerprinted is *unknown*, not *unique*. It is recorded with no fingerprint and
flagged, so it cannot be delivered on the strength of a check that never ran.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum

from . import fingerprint as fp
from .storage import (
    IntegrityError,
    LocalMasterStore,
    MasterStore,
    StorageError,
    sha256_file,
)

# Containers we treat as audio for fingerprinting purposes.
AUDIO_EXTENSIONS = {
    ".wav", ".flac", ".aiff", ".aif", ".alac", ".mp3", ".m4a", ".aac",
    ".ogg", ".opus",
}

# A master below this is almost certainly a mistake or a preview.
MIN_BYTES = 64 * 1024


class UploadError(ValueError):
    """The upload cannot be accepted."""


class DuplicateKind(StrEnum):
    NONE = "none"
    EXACT = "exact"
    FINGERPRINT = "fingerprint"


@dataclass
class UploadResult:
    sha256: str
    storage_key: str
    byte_size: int
    filename: str
    duplicate_of: str | None = None
    duplicate_kind: DuplicateKind = DuplicateKind.NONE
    fingerprint: fp.Fingerprint | None = None
    fingerprint_error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_kind is not DuplicateKind.NONE

    @property
    def is_fingerprinted(self) -> bool:
        return self.fingerprint is not None

    @property
    def blocks_delivery(self) -> bool:
        """Whether this upload must not proceed to delivery without review.

        An unfingerprinted file blocks: it has not been checked for duplicates,
        and 'we did not check' is not 'there are none'.
        """
        return self.is_duplicate or not self.is_fingerprinted


def _looks_like_audio(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in AUDIO_EXTENSIONS


@dataclass
class KnownUpload:
    """A previously stored upload, for duplicate comparison."""

    upload_id: str
    sha256: str
    filename: str
    fingerprint: fp.Fingerprint | None = None


class UploadService:
    """Ingests masters, deduplicating on content and on audio."""

    def __init__(
        self,
        store: MasterStore | None = None,
        fingerprinter=fp.fingerprint_file,
    ):
        self.store = store if store is not None else LocalMasterStore(
            os.environ.get("BOE_MASTER_DIR", "var/masters")
        )
        self._fingerprint_file = fingerprinter

    def ingest(
        self,
        path: str | os.PathLike,
        filename: str,
        known: list[KnownUpload] | None = None,
    ) -> UploadResult:
        """Ingest a master file.

        `known` is the artist's existing uploads to compare against. Passing it
        in keeps this function free of database access and makes the duplicate
        logic testable without one.
        """
        path = str(path)
        if not os.path.isfile(path):
            raise UploadError(f"no such file: {path}")
        if not filename or not filename.strip():
            raise UploadError("filename is required")

        size = os.path.getsize(path)
        if size == 0:
            raise UploadError(f"{filename} is empty")
        if size < MIN_BYTES:
            raise UploadError(
                f"{filename} is {size} bytes, below the {MIN_BYTES}-byte "
                f"minimum for a master"
            )

        digest = sha256_file(path)

        try:
            stored = self.store.put_file(path, filename)
        except StorageError as exc:
            raise UploadError(f"could not store {filename}: {exc}") from exc

        result = UploadResult(
            sha256=digest,
            storage_key=stored.key,
            byte_size=stored.byte_size,
            filename=filename,
        )

        known = known or []

        # Exact duplicate: same bytes, already uploaded.
        exact = next((k for k in known if k.sha256 == digest), None)
        if exact is not None:
            result.duplicate_of = exact.upload_id
            result.duplicate_kind = DuplicateKind.EXACT
            result.warnings.append(
                f"identical to existing upload {exact.upload_id} "
                f"({exact.filename})"
            )
            # Still fingerprint, so the new row carries one. Skipping it would
            # leave a row that cannot be compared later.
            self._fingerprint(path, filename, result)
            return result

        self._fingerprint(path, filename, result)

        # Audio duplicate: different bytes, same recording.
        if result.fingerprint is not None:
            for k in known:
                if k.fingerprint is None:
                    continue
                if fp.is_duplicate(result.fingerprint, k.fingerprint):
                    score = fp.similarity(result.fingerprint, k.fingerprint)
                    result.duplicate_of = k.upload_id
                    result.duplicate_kind = DuplicateKind.FINGERPRINT
                    result.warnings.append(
                        f"sounds like existing upload {k.upload_id} "
                        f"({k.filename}), similarity {score:.4f}"
                    )
                    break

        return result

    def _fingerprint(self, path: str, filename: str, result: UploadResult):
        """Fingerprint the file, recording failure rather than hiding it."""
        if not _looks_like_audio(filename):
            result.warnings.append(
                f"{filename} is not an audio container; not fingerprinted"
            )
            result.fingerprint_error = "not an audio file"
            return

        try:
            result.fingerprint = self._fingerprint_file(path)
        except fp.FpcalcMissing as exc:
            # A deployment problem. The upload is accepted but flagged, and it
            # must not be delivered until the service is fixed.
            result.fingerprint_error = str(exc)
            result.warnings.append(
                f"could not fingerprint {filename}: {exc}"
            )
        except fp.FingerprintError as exc:
            result.fingerprint_error = str(exc)
            result.warnings.append(
                f"could not fingerprint {filename}: {exc}"
            )

    def read_master(self, key: str) -> bytes:
        """Read a stored master back, verifying its content address."""
        return self.store.open(key)

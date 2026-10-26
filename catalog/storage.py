"""Master storage (S2-03).

Audio masters are stored **content-addressed**: the key is the SHA-256 of the
bytes. Three things follow, and all three matter more than they look.

  * Uploading the same master twice is a no-op. The second upload finds the
    object already present and returns the same key. No duplicate bytes, and no
    second copy to keep in sync.
  * The key *is* the integrity check. Re-reading an object and re-hashing it
    proves it has not been corrupted or swapped, which a name-based key cannot.
  * A key never changes meaning. A name like `artist-track-final-v2.wav` gets
    overwritten; a hash cannot.

Storage backends sit behind `MasterStore`. A local directory is enough for
development and for the tests, and the interface is the one an S3 or Supabase
Storage backend needs: put, get, exists, and a URL.

Nothing here deletes. Masters are the one artifact that cannot be regenerated
from anything else in the system.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

# Masters are large; read in chunks rather than slurping a gigabyte into RAM.
CHUNK = 1024 * 1024


class StorageError(RuntimeError):
    """A storage operation failed."""


class IntegrityError(StorageError):
    """Stored bytes do not match their content address.

    Raised rather than returned, because a master whose hash does not match its
    key is corrupt or has been substituted, and every downstream use — the
    fingerprint, the delivered file, the royalty attribution — would be built
    on the wrong audio.
    """


def sha256_file(path: str | os.PathLike) -> str:
    """SHA-256 of a file, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_hash(value: str) -> str:
    value = value.strip().lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise StorageError(f"not a sha256 hex digest: {value!r}")
    return value


def _safe_extension(filename: str) -> str:
    """The extension to preserve alongside the hash.

    Preserved so a delivered file keeps its container type, but sanitised: an
    extension is attacker-controlled input that ends up in a path.
    """
    suffix = Path(filename).suffix.lower()
    if not suffix:
        return ""
    if len(suffix) > 10 or not suffix[1:].isalnum():
        return ""
    return suffix


@dataclass(frozen=True)
class StoredObject:
    key: str
    byte_size: int
    sha256: str
    uri: str

    @property
    def extension(self) -> str:
        suffix = Path(self.key).suffix
        return suffix


class MasterStore(ABC):
    """Content-addressed storage for audio masters."""

    @abstractmethod
    def put_file(self, path: str | os.PathLike, filename: str) -> StoredObject:
        """Store a file and return its content-addressed object."""

    @abstractmethod
    def put_bytes(self, data: bytes, filename: str) -> StoredObject:
        """Store bytes and return their content-addressed object."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def uri_for(self, key: str) -> str:
        ...

    @abstractmethod
    def open(self, key: str) -> bytes:
        """Read an object's bytes, verifying the hash. Raises IntegrityError."""

    def path_for(self, key: str) -> str | None:
        """A local filesystem path, when the backend has one."""
        return None

    def _key_for(self, digest: str, filename: str) -> str:
        """Shard by the first two bytes of the hash.

        A single flat directory with hundreds of thousands of entries is slow
        on most filesystems; two levels of 256 keeps any one directory small.
        """
        digest = _validate_hash(digest)
        return f"masters/{digest[:2]}/{digest[2:4]}/{digest}{_safe_extension(filename)}"


class LocalMasterStore(MasterStore):
    """A content-addressed store on the local filesystem."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, path, filename) -> StoredObject:
        digest = sha256_file(path)
        key = self._key_for(digest, filename)
        dest = self.root / key

        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temporary name and rename, so a crash mid-copy cannot
            # leave a partial file at a content address that claims to be
            # complete.
            tmp = dest.with_suffix(dest.suffix + ".partial")
            shutil.copyfile(path, tmp)
            os.replace(tmp, dest)

        size = dest.stat().st_size
        return StoredObject(key, size, digest, self.uri_for(key))

    def put_bytes(self, data: bytes, filename) -> StoredObject:
        digest = sha256_bytes(data)
        key = self._key_for(digest, filename)
        dest = self.root / key

        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".partial")
            tmp.write_bytes(data)
            os.replace(tmp, dest)

        return StoredObject(key, len(data), digest, self.uri_for(key))

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def uri_for(self, key: str) -> str:
        return f"file://{self.root / key}"

    def path_for(self, key: str) -> str:
        return str(self.root / key)

    def open(self, key: str) -> bytes:
        path = self.root / key
        if not path.exists():
            raise StorageError(f"no object at {key}")
        data = path.read_bytes()
        # The key encodes the expected hash; verify rather than trust.
        expected = Path(key).stem
        actual = sha256_bytes(data)
        if actual != expected:
            raise IntegrityError(
                f"object {key} hashes to {actual}; stored bytes do not match "
                f"their content address"
            )
        return data

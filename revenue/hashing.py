"""Content hashing and the statement manifest.

The hash of a statement file is the anchor for everything downstream: it is what
gets notarised on-chain in Stage 4, and it is how a distribution is later proved
to have been funded by a specific statement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

CHUNK = 1 << 20


def sha256_file(path: str | Path) -> str:
    """Streaming SHA-256 of a file. Never loads the whole file into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    sha256: str
    size: int


@dataclass
class Manifest:
    """Ordered record of every file that made up a statement import."""

    entries: list[ManifestEntry] = field(default_factory=list)

    def add(self, path: str | Path) -> ManifestEntry:
        p = Path(path)
        entry = ManifestEntry(str(p), sha256_file(p), p.stat().st_size)
        self.entries.append(entry)
        return entry

    def root_hash(self) -> str:
        """A single hash over all entries, order-independent on content."""
        h = hashlib.sha256()
        for e in sorted(self.entries, key=lambda x: x.path):
            h.update(f"{e.path}\0{e.sha256}\0{e.size}\n".encode())
        return h.hexdigest()

    def to_json(self) -> str:
        return json.dumps(
            {
                "root_hash": self.root_hash(),
                "entries": [e.__dict__ for e in self.entries],
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path]) -> "Manifest":
        m = cls()
        for p in paths:
            m.add(p)
        return m

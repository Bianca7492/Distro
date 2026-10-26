"""Internal identifiers (S2-06).

    BOE-<KIND>-<SEQ>-<CHECK>

These are our own primary keys. They are **not** ISRCs: ISRC prefix codes are
allocated by the ISRC Agency and self-generated codes are invalid. An ISRC is
stored as an optional attribute when the rights holder already has one.

The generator here mirrors ``next_internal_id`` in the migration exactly. The
database is authoritative — it assigns IDs on insert — but the same algorithm
is needed offline: for fixtures, for the ERN builder, and for validating an ID
that arrives from a client without a round trip.

If you change the algorithm, change both. ``tests/test_catalog_ids.py`` asserts
they agree.
"""

from __future__ import annotations

import re

# Crockford base32: no I, L, O or U, so an ID cannot be misread.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

KINDS = ("TRK", "REL", "WRK", "ART")

_FNV_OFFSET = 2166136261
_FNV_PRIME = 16777619
_MASK32 = 0xFFFFFFFF

PATTERN = re.compile(r"^BOE-(TRK|REL|WRK|ART)-([0-9]{10})-([0-9A-Z]{2})$")


def check_suffix(body: str) -> str:
    """Two-character Crockford base32 check over the ID body.

    FNV-1a, folded to 10 bits. Must match ``boe_check_suffix`` in the
    migration, which does the same with 32-bit wrapping.
    """
    h = _FNV_OFFSET
    for ch in body:
        h = (h ^ ord(ch)) & _MASK32
        h = (h * _FNV_PRIME) & _MASK32
    return ALPHABET[(h >> 5) % 32] + ALPHABET[h % 32]


def format_id(kind: str, seq: int) -> str:
    """Build an ID from a kind and a sequence number."""
    if kind not in KINDS:
        raise ValueError(f"unknown internal id kind: {kind!r}")
    if seq < 1:
        raise ValueError(f"sequence starts at 1, got {seq}")
    body = f"BOE-{kind}-{seq:010d}"
    return f"{body}-{check_suffix(body)}"


def is_valid(internal_id: str) -> bool:
    """True when the ID is well formed *and* its check suffix is correct.

    The check catches a truncated or mistyped ID locally, rather than letting
    it silently resolve to a different row.
    """
    if not isinstance(internal_id, str):
        return False
    m = PATTERN.match(internal_id)
    if not m:
        return False
    body = internal_id[:-3]
    return internal_id[-2:] == check_suffix(body)


def parse(internal_id: str) -> tuple[str, int]:
    """Split an ID into (kind, sequence). Raises on an invalid ID."""
    if not is_valid(internal_id):
        raise ValueError(f"invalid internal id: {internal_id!r}")
    m = PATTERN.match(internal_id)
    assert m is not None
    return m.group(1), int(m.group(2))

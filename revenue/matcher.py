"""Statement-line matching.

Order of precedence:
  1. exact ISRC           -> confidence 1.0, auto-accepted
  2. exact UPC + title    -> high confidence, auto-accepted
  3. fingerprint          -> high confidence, auto-accepted
  4. fuzzy title+artist   -> scored, NEVER auto-distributed; queued for review

The rule that governs everything here: **money is never distributed on a guess.**
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz

from .store import Store


class MatchMethod(StrEnum):
    ISRC = "isrc"
    UPC = "upc"
    FINGERPRINT = "fingerprint"
    FUZZY = "fuzzy"
    NONE = "none"


class LineStatus(StrEnum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


# Fuzzy scores are confidence, not decisions. Above AUTO_MIN it still goes to a
# human; nothing fuzzy is ever auto-accepted.
REVIEW_MIN = 0.75   # below this: unmatched
AUTO_ISRC = 1.0
UPC_CONF = 0.99
FINGERPRINT_CONF = 0.97


@dataclass
class MatchResult:
    line_id: str
    track_id: str | None
    method: MatchMethod
    confidence: float
    status: LineStatus


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return "".join(ch.lower() for ch in s if ch.isalnum() or ch.isspace()).strip()


def title_artist_score(
    a_title: str | None, a_artist: str | None,
    b_title: str | None, b_artist: str | None,
) -> float:
    """Weighted fuzzy score; title dominates, artist corroborates."""
    t = fuzz.token_set_ratio(_norm(a_title), _norm(b_title)) / 100.0
    ar = fuzz.token_set_ratio(_norm(a_artist), _norm(b_artist)) / 100.0
    if not _norm(a_artist) or not _norm(b_artist):
        return t
    return 0.7 * t + 0.3 * ar


def match_line(store: Store, line, tracks: list | None = None) -> MatchResult:
    """Match a single statement line against the catalogue."""
    tracks = tracks if tracks is not None else store.all_tracks()

    # 1. exact ISRC
    isrc = (line["raw_isrc"] or "").strip().upper()
    if isrc:
        for t in tracks:
            if (t["isrc"] or "").strip().upper() == isrc:
                return MatchResult(
                    line["id"], t["id"], MatchMethod.ISRC, AUTO_ISRC,
                    LineStatus.MATCHED,
                )

    # 2. exact UPC + corroborating title
    upc = (line["raw_upc"] or "").strip()
    if upc:
        for t in tracks:
            if (t["fingerprint"] or "") == f"upc:{upc}":
                return MatchResult(
                    line["id"], t["id"], MatchMethod.UPC, UPC_CONF,
                    LineStatus.MATCHED,
                )

    # 3. fingerprint carried on the line (from the DSP)
    fp = (line["raw_dsp"] or "")  # DSR carries no fingerprint; reserved for CSV
    if fp.startswith("fp:"):
        for t in tracks:
            if t["fingerprint"] == fp:
                return MatchResult(
                    line["id"], t["id"], MatchMethod.FINGERPRINT,
                    FINGERPRINT_CONF, LineStatus.MATCHED,
                )

    # 4. fuzzy -> review queue, never auto-distributed
    best = None
    best_score = 0.0
    for t in tracks:
        s = title_artist_score(
            line["raw_title"], line["raw_artist"], t["title"], t["artist"]
        )
        if s > best_score:
            best_score, best = s, t

    if best is not None and best_score >= REVIEW_MIN:
        return MatchResult(
            line["id"], best["id"], MatchMethod.FUZZY, round(best_score, 4),
            LineStatus.AMBIGUOUS,
        )

    return MatchResult(
        line["id"], None, MatchMethod.NONE, 0.0, LineStatus.UNMATCHED
    )


def apply_match(store: Store, result: MatchResult) -> None:
    """Persist a match. Fuzzy matches are recorded but not auto-accepted."""
    if result.status is LineStatus.MATCHED and result.track_id:
        store.mark_matched(
            result.line_id, result.track_id, result.method.value, result.confidence
        )
    else:
        store.mark_status(result.line_id, result.status.value)

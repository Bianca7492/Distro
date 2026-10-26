"""ERN 4.3 message building and parsing (Stage 2 gate).

Builds a NewReleaseMessage from catalog records, and parses one back. The
round trip matters because the Stage 2 gate is a closed loop: we deliver to a
test DSP and ingest the result back. If the builder and the parser disagree,
the loop is not closed, however green the tests are.

References are the crux. DDEX links elements by opaque reference strings —
`PartyReference`, `ResourceReference`, `ReleaseReference` — not by embedding.
Every reference must resolve within the same message, and a dangling one is a
rejected delivery. `validate_references` checks that before the message is
sent, because a DSP's rejection report is a slower and vaguer way to find out.

ISRC and ICPN are carried only when the rights holder supplied them. We never
mint either: both are allocated by external agencies and a self-generated code
is invalid. A release without an ICPN is delivered with a
`ProprietaryId` instead, which is what the standard provides for.

Timing: DDEX durations are ISO 8601 durations (`PT3M30S`), not seconds.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from .splits import SplitError, SplitSheet

ERN_NS = "http://ddex.net/xml/ern/43"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

ERN_VERSION = "ern/43"
SINGLE_PROFILE = "SimpleAudioSingle/23"
ALBUM_PROFILE = "SimpleAudioAlbum/23"

# The AVS version the profile requires. ERN 4.3 makes this a mandatory
# attribute; omitting it fails validation at the DSP.
AVS_VERSION = "2024"

DEFAULT_TERRITORY = "Worldwide"


class ErNError(ValueError):
    """The message cannot be built or is not usable."""


class MessageType(StrEnum):
    ORIGINAL = "OriginalMessage"
    UPDATE = "UpdateMessage"
    TAKEDOWN = "TakedownMessage"


def iso8601_duration(seconds: int) -> str:
    """Seconds to an ISO 8601 duration, e.g. 210 -> PT3M30S."""
    if seconds < 0:
        raise ErNError(f"duration cannot be negative: {seconds}")
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    out = "PT"
    if hours:
        out += f"{hours}H"
    if minutes:
        out += f"{minutes}M"
    if secs or out == "PT":
        out += f"{secs}S"
    return out


def iso8601_datetime(dt: datetime) -> str:
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass
class Party:
    name: str
    # DDEX party identifier, e.g. a DPID. Optional; the name is what shows.
    party_id: str | None = None
    reference: str = ""


@dataclass
class Track:
    title: str
    display_artist: str
    duration_s: int
    internal_id: str
    isrc: str | None = None
    reference: str = ""
    # Splits are validated before the message is built. A track whose splits do
    # not sum to the whole cannot be delivered.
    splits: SplitSheet | None = None


@dataclass
class Release:
    title: str
    display_artist: str
    internal_id: str
    tracks: list[Track] = field(default_factory=list)
    upc: str | None = None
    label: str | None = None
    release_date: datetime | None = None
    reference: str = ""


@dataclass
class MessageConfig:
    message_id: str
    sender_name: str
    recipient_name: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    message_type: MessageType = MessageType.ORIGINAL
    territory: str = DEFAULT_TERRITORY


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _q(tag: str) -> str:
    return f"{{{ERN_NS}}}{tag}"


def _sub(parent, tag: str, text: str | None = None):
    el = ET.SubElement(parent, _q(tag))
    if text is not None:
        el.text = text
    return el


def _validate_release(release: Release) -> None:
    if not release.title.strip():
        raise ErNError("release title is required")
    if not release.display_artist.strip():
        raise ErNError("release display artist is required")
    if not release.tracks:
        raise ErNError("a release needs at least one track")
    if not release.internal_id.strip():
        raise ErNError("release internal_id is required")

    seen = set()
    for t in release.tracks:
        if not t.title.strip():
            raise ErNError("track title is required")
        if not t.display_artist.strip():
            raise ErNError(f"track {t.title!r} has no display artist")
        if t.duration_s <= 0:
            raise ErNError(f"track {t.title!r} has no duration")
        if not t.internal_id.strip():
            raise ErNError(f"track {t.title!r} has no internal_id")
        if t.internal_id in seen:
            raise ErNError(f"duplicate track internal_id {t.internal_id}")
        seen.add(t.internal_id)
        # Splits must be complete before delivery. An ERN whose splits do not
        # sum to the whole is rejected, and guessing the remainder would pay
        # the wrong party.
        if t.splits is not None:
            try:
                t.splits.validate_for_delivery()
            except SplitError as exc:
                raise ErNError(
                    f"track {t.title!r} has incomplete splits: {exc}"
                ) from exc

    # A UPC that is present must look like a UPC. A malformed one is worse
    # than none: the DSP would reject the delivery.
    if release.upc is not None:
        digits = release.upc.strip()
        if not digits.isdigit() or len(digits) not in (12, 13):
            raise ErNError(
                f"UPC must be 12 or 13 digits, got {release.upc!r}"
            )


def _validate_isrc(isrc: str) -> None:
    """ISRC is CC-XXX-YY-NNNNN with 12 alphanumerics once punctuation is
    removed. We check the shape only; we do not assign or repair codes."""
    compact = "".join(c for c in isrc if c.isalnum()).upper()
    if len(compact) != 12:
        raise ErNError(
            f"ISRC must have 12 alphanumeric characters, got {isrc!r}"
        )
    if not compact[:2].isalpha() or not compact[2:5].isalnum():
        raise ErNError(f"ISRC has a malformed country or registrant: {isrc!r}")
    if not compact[5:].isdigit():
        raise ErNError(f"ISRC has a malformed designation code: {isrc!r}")


def build_ern(release: Release, config: MessageConfig) -> str:
    """Build an ERN 4.3 NewReleaseMessage as an XML string."""
    _validate_release(release)

    profile = SINGLE_PROFILE if len(release.tracks) == 1 else ALBUM_PROFILE

    ET.register_namespace("ern", ERN_NS)
    ET.register_namespace("xsi", XSI_NS)

    root = ET.Element(
        _q("NewReleaseMessage"),
        {
            "MessageSchemaVersionId": ERN_VERSION,
            "ReleaseProfileVersionId": profile,
            "AvsVersionId": AVS_VERSION,
            "LanguageAndScriptCode": "en",
            "UpdateIndicator": config.message_type.value,
            f"{{{XSI_NS}}}schemaLocation": (
                f"{ERN_NS} "
                "http://ddex.net/xml/ern/43/release-notification.xsd"
            ),
        },
    )

    # --- MessageHeader ---
    header = _sub(root, "MessageHeader")
    _sub(header, "MessageThreadId", config.message_id)
    _sub(header, "MessageId", config.message_id)
    sender = _sub(header, "MessageSender")
    _sub(_sub(sender, "PartyName"), "FullName", config.sender_name)
    recipient = _sub(header, "MessageRecipient")
    _sub(_sub(recipient, "PartyName"), "FullName", config.recipient_name)
    _sub(
        header,
        "MessageCreatedDateTime",
        iso8601_datetime(config.created_at),
    )

    # --- PartyList ---
    # Party references and resource references are separate namespaces in
    # DDEX. They must not share a string, or a resolver could bind a party
    # reference to a resource.
    parties: list[Party] = []
    if release.label:
        parties.append(Party(name=release.label, reference="P-label"))
    for i, t in enumerate(release.tracks, start=1):
        parties.append(
            Party(name=t.display_artist, reference=f"P-artist-{i}")
        )
    # The release-level artist may not be a track artist.
    if not any(p.name == release.display_artist for p in parties):
        parties.insert(0, Party(name=release.display_artist, reference="P-rel"))

    party_list = _sub(root, "PartyList")
    for p in parties:
        el = _sub(party_list, "Party")
        _sub(el, "PartyReference", p.reference)
        _sub(_sub(el, "PartyName"), "FullName", p.name)
        if p.party_id:
            _sub(el, "PartyId", p.party_id)

    # --- ResourceList ---
    resource_list = _sub(root, "ResourceList")
    for t in release.tracks:
        sr = _sub(resource_list, "SoundRecording")
        _sub(sr, "SoundRecordingType", "MusicalWorkSoundRecording")
        _sub(sr, "ResourceReference", t.reference)

        if t.isrc:
            _validate_isrc(t.isrc)
            _sub(_sub(sr, "ResourceId"), "ISRC", t.isrc)
        else:
            # No ISRC. We do not invent one. The internal ID goes in as a
            # proprietary identifier so the recording is still addressable.
            pid = _sub(_sub(sr, "ResourceId"), "ProprietaryId")
            pid.set("Namespace", "BOE")
            pid.text = t.internal_id

        _sub(_sub(sr, "ReferenceTitle"), "TitleText", t.title)
        _sub(sr, "Duration", iso8601_duration(t.duration_s))

        details = _sub(sr, "SoundRecordingDetailsByTerritory")
        _sub(details, "TerritoryCode", config.territory)
        _sub(details, "DisplayArtistName", t.display_artist)

    # --- ReleaseList ---
    release_list = _sub(root, "ReleaseList")
    rel = _sub(release_list, "Release")

    if release.upc:
        _sub(_sub(rel, "ReleaseId"), "ICPN", release.upc)
    else:
        pid = _sub(_sub(rel, "ReleaseId"), "ProprietaryId")
        pid.set("Namespace", "BOE")
        pid.text = release.internal_id

    _sub(rel, "ReleaseReference", release.reference)
    _sub(_sub(rel, "ReferenceTitle"), "TitleText", release.title)

    if release.release_date:
        _sub(
            rel,
            "ReleaseDate",
            release.release_date.astimezone(timezone.utc).strftime("%Y-%m-%d"),
        )

    resource_refs = _sub(rel, "ReleaseResourceReferenceList")
    for t in release.tracks:
        rrr = _sub(resource_refs, "ReleaseResourceReference", t.reference)
        rrr.set("ReleaseResourceType", "PrimaryResource")

    rel_details = _sub(rel, "ReleaseDetailsByTerritory")
    _sub(rel_details, "TerritoryCode", config.territory)
    _sub(rel_details, "DisplayArtistName", release.display_artist)
    if release.label:
        _sub(rel_details, "LabelName", release.label)

    _indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _indent(elem, level: int = 0) -> None:
    """Indent in place, for a readable message. A DSP does not care, but a
    human debugging a rejected delivery does."""
    pad = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + "  "
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = pad
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = pad


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class ParsedMessage:
    message_id: str
    sender: str
    recipient: str
    created_at: str
    release_title: str
    release_reference: str
    upc: str | None
    tracks: list[dict]
    parties: list[str]

    @property
    def track_count(self) -> int:
        return len(self.tracks)


def _text(el, tag: str) -> str | None:
    found = el.find(_q(tag))
    return found.text.strip() if found is not None and found.text else None


def parse_ern(xml: str) -> ParsedMessage:
    """Parse an ERN 4.3 message back into a summary.

    Tolerant about element order and optional elements, strict about the parts
    the loop depends on: the message id, and the release with its resources.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ErNError(f"message is not well-formed XML: {exc}") from exc

    if not root.tag.endswith("NewReleaseMessage"):
        raise ErNError(f"not a NewReleaseMessage: {root.tag}")

    version = root.get("MessageSchemaVersionId")
    if version != ERN_VERSION:
        raise ErNError(
            f"expected {ERN_VERSION}, got {version!r}"
        )

    header = root.find(_q("MessageHeader"))
    if header is None:
        raise ErNError("message has no MessageHeader")

    message_id = _text(header, "MessageId")
    if not message_id:
        raise ErNError("message has no MessageId")

    def party_name(el) -> str:
        if el is None:
            return ""
        name = el.find(_q("PartyName"))
        return _text(name, "FullName") or "" if name is not None else ""

    created = _text(header, "MessageCreatedDateTime") or ""

    parties = [
        _text(p, "PartyReference") or ""
        for p in root.findall(f"{_q('PartyList')}/{_q('Party')}")
    ]

    release_list = root.find(_q("ReleaseList"))
    if release_list is None:
        raise ErNError("message has no ReleaseList")
    rel = release_list.find(_q("Release"))
    if rel is None:
        raise ErNError("message has no Release")

    rel_id = rel.find(_q("ReleaseId"))
    upc = _text(rel_id, "ICPN") if rel_id is not None else None

    title_el = rel.find(_q("ReferenceTitle"))
    release_title = _text(title_el, "TitleText") or "" if title_el is not None else ""

    # Resources, indexed by reference.
    resources: dict[str, dict] = {}
    for sr in root.findall(f"{_q('ResourceList')}/{_q('SoundRecording')}"):
        ref = _text(sr, "ResourceReference")
        if not ref:
            raise ErNError("a SoundRecording has no ResourceReference")
        rid = sr.find(_q("ResourceId"))
        resources[ref] = {
            "reference": ref,
            "title": _text(sr.find(_q("ReferenceTitle")), "TitleText"),
            "isrc": _text(rid, "ISRC") if rid is not None else None,
            "proprietary_id": (
                _text(rid, "ProprietaryId") if rid is not None else None
            ),
            "duration": _text(sr, "Duration"),
        }

    # Release resource references, in order.
    tracks = []
    rrr_list = rel.find(_q("ReleaseResourceReferenceList"))
    if rrr_list is None:
        raise ErNError("Release has no ReleaseResourceReferenceList")
    for rrr in rrr_list.findall(_q("ReleaseResourceReference")):
        ref = (rrr.text or "").strip()
        if ref not in resources:
            raise ErNError(
                f"ReleaseResourceReference {ref!r} resolves to no resource"
            )
        tracks.append(resources[ref])

    if not tracks:
        raise ErNError("Release references no resources")

    return ParsedMessage(
        message_id=message_id,
        sender=party_name(header.find(_q("MessageSender"))),
        recipient=party_name(header.find(_q("MessageRecipient"))),
        created_at=created,
        release_title=release_title,
        release_reference=_text(rel, "ReleaseReference") or "",
        upc=upc,
        tracks=tracks,
        parties=parties,
    )


def validate_references(xml: str) -> list[str]:
    """Check that every reference in a message resolves. Returns problems.

    Run before delivery. A dangling reference is the most common reason a DSP
    rejects an otherwise well-formed message, and a rejection report is a slow
    way to discover one.
    """
    problems: list[str] = []
    root = ET.fromstring(xml)

    declared_parties = {
        _text(p, "PartyReference")
        for p in root.findall(f"{_q('PartyList')}/{_q('Party')}")
    }
    declared_parties.discard(None)

    resources = {
        _text(sr, "ResourceReference")
        for sr in root.findall(f"{_q('ResourceList')}/{_q('SoundRecording')}")
    }
    resources.discard(None)

    if not declared_parties:
        problems.append("PartyList is empty")

    # A party reference must not resolve to a resource, and vice versa.
    overlap = declared_parties & resources
    if overlap:
        problems.append(
            f"party and resource references overlap: {sorted(overlap)}"
        )

    for rel in root.findall(f"{_q('ReleaseList')}/{_q('Release')}"):
        ref = _text(rel, "ReleaseReference")
        if not ref:
            problems.append("a Release has no ReleaseReference")
        rrr_list = rel.find(_q("ReleaseResourceReferenceList"))
        if rrr_list is None:
            problems.append(f"Release {ref} has no ReleaseResourceReferenceList")
            continue
        for rrr in rrr_list.findall(_q("ReleaseResourceReference")):
            target = (rrr.text or "").strip()
            if target not in resources:
                problems.append(
                    f"Release {ref} references unknown resource {target!r}"
                )

    if not resources:
        problems.append("ResourceList has no SoundRecording")

    return problems

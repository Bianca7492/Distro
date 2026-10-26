"""ERN 4.3 building, parsing, and the delivery loop (Stage 2 gate).

The gate is a closed loop: deliver an ERN to a test DSP and ingest it back.
These tests exercise both halves. The round trip is the one that matters — a
builder and parser that each pass their own tests but disagree with each other
do not close the loop.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from catalog.delivery import (
    DeliveryError,
    DeliveryService,
    DeliveryStatus,
    InMemoryDsp,
    parse_receipt,
)
from catalog.ern import (
    ALBUM_PROFILE,
    AVS_VERSION,
    ERN_VERSION,
    SINGLE_PROFILE,
    ErNError,
    MessageConfig,
    MessageType,
    Release,
    Track,
    build_ern,
    iso8601_duration,
    parse_ern,
    validate_references,
)
from catalog.splits import Split, SplitSheet


def track(title="Song", artist="Alice", seconds=210, isrc=None, ref="R1",
          splits=None, internal_id="BOE-TRK-0000000001-HH"):
    return Track(
        title=title,
        display_artist=artist,
        duration_s=seconds,
        internal_id=internal_id,
        isrc=isrc,
        reference=ref,
        splits=splits,
    )


def config(**kwargs):
    defaults = dict(
        message_id="MSG-1",
        sender_name="BOE Distro",
        recipient_name="Stardust Test DSP",
    )
    defaults.update(kwargs)
    return MessageConfig(**defaults)


def release(tracks=None, **kwargs):
    tracks = tracks if tracks is not None else [track()]
    defaults = dict(
        title="Test Single",
        display_artist="Alice",
        internal_id="BOE-REL-0000000001-FN",
        tracks=tracks,
        upc="0123456789012",
        label="BOE Records",
        reference="REL-1",
    )
    defaults.update(kwargs)
    return Release(**defaults)


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "PT0S"),
        (30, "PT30S"),
        (60, "PT1M"),
        (210, "PT3M30S"),
        (3600, "PT1H"),
        (3661, "PT1H1M1S"),
        (7325, "PT2H2M5S"),
    ],
)
def test_iso8601_durations(seconds, expected):
    assert iso8601_duration(seconds) == expected


def test_negative_duration_is_rejected():
    with pytest.raises(ErNError):
        iso8601_duration(-1)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def test_a_single_track_uses_the_single_profile():
    xml = build_ern(release(), config())
    assert SINGLE_PROFILE in xml


def test_multiple_tracks_use_the_album_profile():
    tracks = [
        track(ref="R1", internal_id="BOE-TRK-0000000001-HH"),
        track(title="Two", ref="R2", internal_id="BOE-TRK-0000000002-BR"),
    ]
    xml = build_ern(release(tracks=tracks), config())
    assert ALBUM_PROFILE in xml


def test_the_message_declares_the_schema_and_avs_version():
    """AvsVersionId is mandatory in ERN 4.3; omitting it fails at the DSP."""
    xml = build_ern(release(), config())
    assert ERN_VERSION in xml
    assert AVS_VERSION in xml


def test_the_message_is_well_formed_xml():
    import xml.etree.ElementTree as ET

    ET.fromstring(build_ern(release(), config()))


def test_an_isrc_is_carried_when_supplied():
    xml = build_ern(release(tracks=[track(isrc="USSM19803037")]), config())
    assert "USSM19803037" in xml


def test_a_missing_isrc_uses_a_proprietary_id_not_a_minted_code():
    """The gate requires no self-minted ISRCs. Without a real one, the internal
    ID is carried as a proprietary identifier instead."""
    xml = build_ern(release(), config())
    assert "<ern:ISRC>" not in xml
    assert "BOE-TRK-0000000001-HH" in xml
    assert "ProprietaryId" in xml


def test_a_malformed_isrc_is_rejected():
    for bad in ("FAKE0000001", "USSM1980303", "123456789012", "USSM1980303X"):
        with pytest.raises(ErNError):
            build_ern(release(tracks=[track(isrc=bad)]), config())


def test_an_isrc_with_punctuation_is_accepted():
    """ISRCs are written CC-XXX-YY-NNNNN. Punctuation is not part of the code."""
    xml = build_ern(
        release(tracks=[track(isrc="US-SM1-98-03037")]), config()
    )
    assert "US-SM1-98-03037" in xml


def test_a_release_without_a_upc_uses_a_proprietary_id():
    xml = build_ern(release(upc=None), config())
    assert "<ern:ICPN>" not in xml
    assert "BOE-REL-0000000001-FN" in xml


def test_a_malformed_upc_is_rejected():
    for bad in ("12345", "abcdefghijkl", "12345678901234"):
        with pytest.raises(ErNError):
            build_ern(release(upc=bad), config())


def test_an_empty_release_is_rejected():
    with pytest.raises(ErNError):
        build_ern(release(tracks=[]), config())


def test_a_release_without_a_title_is_rejected():
    with pytest.raises(ErNError):
        build_ern(release(title="  "), config())


def test_a_track_without_a_duration_is_rejected():
    with pytest.raises(ErNError):
        build_ern(release(tracks=[track(seconds=0)]), config())


def test_duplicate_track_ids_are_rejected():
    tracks = [
        track(ref="R1", internal_id="BOE-TRK-0000000001-HH"),
        track(title="Two", ref="R2", internal_id="BOE-TRK-0000000001-HH"),
    ]
    with pytest.raises(ErNError) as exc:
        build_ern(release(tracks=tracks), config())
    assert "duplicate" in str(exc.value)


def test_incomplete_splits_block_delivery():
    """An ERN whose splits do not sum to the whole is rejected by the DSP, and
    guessing the remainder would pay the wrong party."""
    sheet = SplitSheet([Split("Alice", "songwriter", 6000)])
    with pytest.raises(ErNError) as exc:
        build_ern(release(tracks=[track(splits=sheet)]), config())
    assert "10000" in str(exc.value)


def test_complete_splits_do_not_block_delivery():
    sheet = SplitSheet(
        [
            Split("Alice", "songwriter", 6000),
            Split("Bob", "producer", 4000),
        ]
    )
    build_ern(release(tracks=[track(splits=sheet)]), config())


def test_update_indicator_is_set():
    xml = build_ern(
        release(), config(message_type=MessageType.UPDATE)
    )
    assert "UpdateMessage" in xml
    xml = build_ern(release(), config())
    assert "OriginalMessage" in xml


def test_parties_are_collected_in_the_party_list():
    xml = build_ern(release(), config())
    assert "BOE Distro" in xml
    assert "Stardust Test DSP" in xml
    assert "Alice" in xml
    assert "BOE Records" in xml


def test_release_date_is_formatted_as_a_date():
    when = datetime(2026, 3, 1, tzinfo=timezone.utc)
    xml = build_ern(release(release_date=when), config())
    assert "2026-03-01" in xml


# ---------------------------------------------------------------------------
# Reference integrity
# ---------------------------------------------------------------------------

def test_a_built_message_has_no_dangling_references():
    assert validate_references(build_ern(release(), config())) == []


def test_a_dangling_resource_reference_is_caught():
    xml = build_ern(release(), config())
    # Point the release's resource reference at a resource that does not exist.
    broken = xml.replace(
        'ReleaseResourceType="PrimaryResource">R1<',
        'ReleaseResourceType="PrimaryResource">R9<',
    )
    assert validate_references(broken)


def test_validate_references_reports_an_empty_party_list():
    xml = build_ern(release(), config())
    import re

    stripped = re.sub(r"<ern:PartyList>.*?</ern:PartyList>", "", xml, flags=re.S)
    problems = validate_references(stripped)
    assert any("PartyList" in p for p in problems)


# ---------------------------------------------------------------------------
# Round trip: the loop
# ---------------------------------------------------------------------------

def test_a_message_parses_back_to_what_was_built():
    xml = build_ern(release(), config())
    parsed = parse_ern(xml)
    assert parsed.message_id == "MSG-1"
    assert parsed.release_title == "Test Single"
    assert parsed.track_count == 1
    assert parsed.tracks[0]["title"] == "Song"
    assert parsed.upc == "0123456789012"


def test_an_isrc_survives_the_round_trip():
    xml = build_ern(release(tracks=[track(isrc="USSM19803037")]), config())
    assert parse_ern(xml).tracks[0]["isrc"] == "USSM19803037"


def test_a_proprietary_id_survives_the_round_trip():
    xml = build_ern(release(), config())
    assert parse_ern(xml).tracks[0]["proprietary_id"] == "BOE-TRK-0000000001-HH"


def test_track_order_survives_the_round_trip():
    tracks = [
        track(title="One", ref="R1", internal_id="BOE-TRK-0000000001-HH"),
        track(title="Two", ref="R2", internal_id="BOE-TRK-0000000002-BR"),
        track(title="Three", ref="R3", internal_id="BOE-TRK-0000000003-XX"),
    ]
    parsed = parse_ern(build_ern(release(tracks=tracks), config()))
    assert [t["title"] for t in parsed.tracks] == ["One", "Two", "Three"]


def test_a_multi_track_message_round_trips_completely():
    tracks = [
        track(title="One", ref="R1", internal_id="BOE-TRK-0000000001-HH"),
        track(title="Two", ref="R2", internal_id="BOE-TRK-0000000002-BR"),
    ]
    parsed = parse_ern(build_ern(release(tracks=tracks), config()))
    assert parsed.track_count == 2


def test_parsing_rejects_a_wrong_version():
    xml = build_ern(release(), config()).replace("ern/43", "ern/42")
    with pytest.raises(ErNError):
        parse_ern(xml)


def test_parsing_rejects_malformed_xml():
    with pytest.raises(ErNError):
        parse_ern("<not xml")


def test_parsing_rejects_a_non_ern_message():
    with pytest.raises(ErNError):
        parse_ern("<Foo></Foo>")


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def test_a_delivery_that_is_accepted_closes_the_loop():
    dsp = InMemoryDsp()
    svc = DeliveryService(dsp)
    delivery = svc.deliver(release(), config())
    assert delivery.status is DeliveryStatus.ACCEPTED
    assert delivery.receipt.accepted
    assert delivery.is_closed


def test_the_dsp_receives_the_exact_bytes_that_were_hashed():
    dsp = InMemoryDsp()
    svc = DeliveryService(dsp)
    delivery = svc.deliver(release(), config())
    sent_id, sent_xml = dsp.received[0]
    assert sent_id == "MSG-1"
    assert sent_xml == delivery.xml
    import hashlib

    assert hashlib.sha256(sent_xml.encode()).hexdigest() == delivery.sha256


def test_a_rejected_delivery_is_not_downgraded():
    dsp = InMemoryDsp(
        reject_with=[{"code": "InvalidUPC", "detail": "bad check digit"}]
    )
    svc = DeliveryService(dsp)
    delivery = svc.deliver(release(), config())
    assert delivery.status is DeliveryStatus.REJECTED
    assert not delivery.receipt.accepted
    assert delivery.is_closed


def test_an_accepted_receipt_carrying_errors_is_treated_as_rejected():
    """Contradictory receipt. Trust the errors: acting on a false acceptance
    distributes a release the DSP did not take."""
    receipt = parse_receipt(
        '{"MessageId":"MSG-1","Status":"Acknowledged","Errors":[{"code":"X"}]}',
        "MSG-1",
    )
    assert receipt.status is DeliveryStatus.REJECTED
    assert not receipt.accepted


def test_a_receipt_for_another_message_is_a_failure():
    """Otherwise one artist's acceptance could be filed against another's
    delivery."""
    receipt = parse_receipt(
        '{"MessageId":"MSG-999","Status":"Acknowledged","Errors":[]}', "MSG-1"
    )
    assert receipt.status is DeliveryStatus.FAILED
    assert not receipt.accepted


def test_an_unknown_status_is_a_failure_not_an_acceptance():
    receipt = parse_receipt(
        '{"MessageId":"MSG-1","Status":"Whatever","Errors":[]}', "MSG-1"
    )
    assert receipt.status is DeliveryStatus.FAILED
    assert not receipt.accepted


def test_an_unparseable_receipt_is_a_failure():
    receipt = parse_receipt("not json at all", "MSG-1")
    assert receipt.status is DeliveryStatus.FAILED
    assert not receipt.accepted


def test_no_acknowledgment_is_not_an_acceptance():
    class SilentDsp(InMemoryDsp):
        def send(self, message_id, xml):
            return ""

    svc = DeliveryService(SilentDsp())
    delivery = svc.deliver(release(), config())
    assert delivery.status is DeliveryStatus.UNACKNOWLEDGED
    assert not delivery.is_closed


def test_a_transport_failure_is_recorded_not_raised():
    class BrokenDsp(InMemoryDsp):
        def send(self, message_id, xml):
            raise ConnectionError("connection refused")

    svc = DeliveryService(BrokenDsp())
    delivery = svc.deliver(release(), config())
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.receipt.errors[0]["code"] == "TransportError"


def test_a_message_with_dangling_references_is_never_sent(monkeypatch):
    """Refuse locally rather than buy a rejection report."""
    import catalog.delivery as delivery_mod

    original = delivery_mod.build_ern

    def broken(release, config):
        return original(release, config).replace(
            'ReleaseResourceType="PrimaryResource">R1<',
            'ReleaseResourceType="PrimaryResource">R9<',
        )

    monkeypatch.setattr(delivery_mod, "build_ern", broken)

    dsp = InMemoryDsp()
    svc = DeliveryService(dsp)
    with pytest.raises(DeliveryError) as exc:
        svc.deliver(release(), config())
    assert "reference" in str(exc.value).lower()
    assert dsp.received == []


def test_the_delivery_is_recorded_with_its_hash_and_log():
    svc = DeliveryService(InMemoryDsp())
    delivery = svc.deliver(release(), config())
    assert len(delivery.sha256) == 64
    assert delivery.log
    assert svc.deliveries == [delivery]


def test_ingesting_the_message_back_reports_what_a_dsp_would_see():
    svc = DeliveryService(InMemoryDsp())
    delivery = svc.deliver(release(), config())
    back = svc.ingest_back(delivery.xml)
    assert back["message_id"] == "MSG-1"
    assert back["track_count"] == 1
    assert back["tracks"][0]["title"] == "Song"


def test_the_full_loop_closes_for_a_multi_track_release():
    """The Stage 2 gate, in one test: build, deliver, accept, ingest back."""
    tracks = [
        track(title=f"Track {i}", ref=f"R{i}",
              internal_id=f"BOE-TRK-{i:010d}-HH")
        for i in range(1, 4)
    ]
    dsp = InMemoryDsp()
    svc = DeliveryService(dsp)

    delivery = svc.deliver(release(tracks=tracks), config())
    assert delivery.status is DeliveryStatus.ACCEPTED

    back = svc.ingest_back(delivery.xml)
    assert back["track_count"] == 3
    assert [t["title"] for t in back["tracks"]] == [
        "Track 1", "Track 2", "Track 3",
    ]
    # And the DSP actually holds the message.
    assert len(dsp.received) == 1

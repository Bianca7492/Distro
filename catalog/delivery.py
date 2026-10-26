"""Delivery loop (Stage 2 gate).

The gate is a closed loop: deliver an ERN to a test DSP and ingest the result
back. A delivery that is sent but never acknowledged is not a closed loop, so
this module treats the acknowledgment as the point of the exercise.

The transport is behind `DspTransport`, so the same delivery logic runs against
an HTTP DSP, against the local test DSP, and against a recorded transcript in
tests. What does not vary:

  * The message is reference-checked before it leaves. A dangling reference is
    the usual reason a delivery is rejected, and finding out from a rejection
    report is slower than finding out locally.
  * The delivery is recorded with the exact bytes and their hash. When a DSP
    later disputes what it received, the transcript is the evidence.
  * A rejection is a rejection. It is never downgraded to a warning, and a
    missing acknowledgment is never treated as an acceptance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from .ern import ErNError, MessageConfig, Release, build_ern, parse_ern, validate_references


class DeliveryError(RuntimeError):
    """The delivery could not be completed."""


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    UNACKNOWLEDGED = "unacknowledged"


@dataclass
class DeliveryReceipt:
    """What the DSP said back.

    `message_id` echoes the message the receipt is for. A receipt whose id does
    not match the delivery is not a receipt for it, and is treated as a failure
    rather than a success — otherwise one artist's acceptance could be filed
    against another's delivery.
    """

    message_id: str
    status: DeliveryStatus
    accepted: bool = False
    errors: list[dict] = field(default_factory=list)
    raw: str | None = None
    received_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class Delivery:
    message_id: str
    xml: str
    sha256: str
    status: DeliveryStatus = DeliveryStatus.PENDING
    receipt: DeliveryReceipt | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    attempts: int = 0
    log: list[str] = field(default_factory=list)

    @property
    def is_closed(self) -> bool:
        """Whether the loop is closed: accepted, or rejected with a reason."""
        return self.status in (
            DeliveryStatus.ACCEPTED,
            DeliveryStatus.REJECTED,
        )


class DspTransport:
    """Where an ERN goes and what comes back."""

    def send(self, message_id: str, xml: str) -> str:
        """Send the message and return the raw acknowledgment body."""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return type(self).__name__


class InMemoryDsp(DspTransport):
    """A DSP that accepts everything, for tests and local runs.

    It records what it received and can be told to reject, so the rejection
    path is exercised rather than assumed.
    """

    def __init__(self, reject_with: list[dict] | None = None):
        self.received: list[tuple[str, str]] = []
        self.reject_with = reject_with

    @property
    def name(self) -> str:
        return "in-memory-dsp"

    def send(self, message_id: str, xml: str) -> str:
        self.received.append((message_id, xml))
        if self.reject_with:
            return json.dumps(
                {
                    "MessageId": message_id,
                    "Status": "Rejected",
                    "Errors": self.reject_with,
                }
            )
        return json.dumps(
            {"MessageId": message_id, "Status": "Acknowledged", "Errors": []}
        )


def parse_receipt(raw: str, expected_message_id: str) -> DeliveryReceipt:
    """Interpret a DSP acknowledgment.

    Anything unrecognised is a failure, not a success. Defaulting an unknown
    status to accepted would close the loop on a response we did not
    understand, which is the one outcome worse than a visible error.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return DeliveryReceipt(
            message_id=expected_message_id,
            status=DeliveryStatus.FAILED,
            accepted=False,
            errors=[{"code": "UnparseableReceipt", "detail": raw[:400]}],
            raw=raw,
        )

    returned_id = str(data.get("MessageId") or data.get("messageId") or "")
    if returned_id and returned_id != expected_message_id:
        return DeliveryReceipt(
            message_id=expected_message_id,
            status=DeliveryStatus.FAILED,
            accepted=False,
            errors=[
                {
                    "code": "MessageIdMismatch",
                    "detail": (
                        f"receipt is for {returned_id!r}, delivery is "
                        f"{expected_message_id!r}"
                    ),
                }
            ],
            raw=raw,
        )

    status_raw = str(data.get("Status") or data.get("status") or "").lower()
    errors = data.get("Errors") or data.get("errors") or []

    if status_raw in ("acknowledged", "accepted", "success"):
        # An "accepted" receipt carrying errors is contradictory. Trust the
        # errors, because acting on a false acceptance distributes a release
        # the DSP did not actually take.
        if errors:
            return DeliveryReceipt(
                message_id=expected_message_id,
                status=DeliveryStatus.REJECTED,
                accepted=False,
                errors=errors,
                raw=raw,
            )
        return DeliveryReceipt(
            message_id=expected_message_id,
            status=DeliveryStatus.ACCEPTED,
            accepted=True,
            raw=raw,
        )

    if status_raw in ("rejected", "failed", "error"):
        return DeliveryReceipt(
            message_id=expected_message_id,
            status=DeliveryStatus.REJECTED,
            accepted=False,
            errors=errors or [{"code": "Rejected", "detail": "no detail given"}],
            raw=raw,
        )

    return DeliveryReceipt(
        message_id=expected_message_id,
        status=DeliveryStatus.FAILED,
        accepted=False,
        errors=[
            {
                "code": "UnknownStatus",
                "detail": f"unrecognised status {status_raw!r}",
            }
        ],
        raw=raw,
    )


class DeliveryService:
    """Builds, checks, sends, and records deliveries."""

    def __init__(self, transport: DspTransport):
        self.transport = transport
        self.deliveries: list[Delivery] = []

    def deliver(
        self, release: Release, config: MessageConfig
    ) -> Delivery:
        """Deliver a release. Returns the delivery with its outcome."""
        try:
            xml = build_ern(release, config)
        except ErNError as exc:
            raise DeliveryError(f"could not build the message: {exc}") from exc

        problems = validate_references(xml)
        if problems:
            # Refuse locally. Sending a message with dangling references just
            # buys a rejection report.
            raise DeliveryError(
                "message has unresolved references: " + "; ".join(problems)
            )

        digest = hashlib.sha256(xml.encode("utf-8")).hexdigest()
        delivery = Delivery(message_id=config.message_id, xml=xml, sha256=digest)
        delivery.attempts = 1
        delivery.log.append(
            f"built {len(release.tracks)}-track message, sha256 {digest[:12]}"
        )
        self.deliveries.append(delivery)

        try:
            raw = self.transport.send(config.message_id, xml)
        except Exception as exc:
            delivery.status = DeliveryStatus.FAILED
            delivery.log.append(f"transport failed: {exc}")
            delivery.receipt = DeliveryReceipt(
                message_id=config.message_id,
                status=DeliveryStatus.FAILED,
                errors=[{"code": "TransportError", "detail": str(exc)}],
            )
            return delivery

        if not raw or not raw.strip():
            # Sent but nothing came back. That is not an acceptance.
            delivery.status = DeliveryStatus.UNACKNOWLEDGED
            delivery.log.append("no acknowledgment returned")
            return delivery

        receipt = parse_receipt(raw, config.message_id)
        delivery.receipt = receipt
        delivery.status = receipt.status
        delivery.log.append(f"receipt: {receipt.status.value}")
        return delivery

    def ingest_back(self, xml: str) -> dict:
        """Parse a delivered message back, as the DSP would.

        This is the return half of the loop. It parses the message we sent and
        reports what a DSP would see, which is how a builder/parser mismatch is
        caught here rather than in production.
        """
        try:
            parsed = parse_ern(xml)
        except ErNError as exc:
            raise DeliveryError(f"could not ingest the message back: {exc}") from exc
        return {
            "message_id": parsed.message_id,
            "release_title": parsed.release_title,
            "track_count": parsed.track_count,
            "tracks": [
                {
                    "title": t["title"],
                    "isrc": t["isrc"],
                    "proprietary_id": t["proprietary_id"],
                    "duration": t["duration"],
                }
                for t in parsed.tracks
            ],
        }

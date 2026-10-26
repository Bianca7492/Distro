"""DDEX DSR (Digital Sales Report) parser.

Handles the real DDEX SalesReportMessage structure. Namespace-aware, and uses
``iterparse`` with ``elem.clear()`` so a large statement does not exhaust memory
(the failure mode documented in ``music-metadata``).

DDEX DSR 4.x namespace: ``http://ddex.net/xml/2019/dsr``
DDEX DSR 3.x namespace: ``http://ddex.net/xml/2014/dsr``
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from lxml import etree

from .money import Money
from .store import StatementLine

# Namespaces seen across DSR 3.x / 4.x / flat-file XML exports.
DSR_NAMESPACES = (
    "http://ddex.net/xml/2019/dsr",
    "http://ddex.net/xml/2014/dsr",
    "http://ddex.net/xml/dsr",
    "http://ddex.net/xml/2019/dsr-flat",
    "http://ddex.net/xml/2014/dsr-flat",
)


class DSRError(ValueError):
    """Raised when a file is not a parseable DSR message."""


@dataclass
class DSRHeader:
    source: str
    period_start: str
    period_end: str
    currency: str
    message_version: str


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(el, path: str) -> str | None:
    """Namespace-agnostic child text lookup."""
    if el is None:
        return None
    for child in el.iter():
        if _localname(child.tag) == path and child.text:
            return child.text.strip()
    return None


def _iter_named(root, name: str) -> Iterator:
    for el in root.iter():
        if _localname(el.tag) == name:
            yield el


def detect_namespace(path: str | Path) -> str:
    """Return the DSR namespace declared by the document root."""
    for _, (prefix, uri) in etree.iterparse(
        str(path), events=("start-ns",)
    ):
        if "dsr" in uri:
            return uri
    raise DSRError(f"no DSR namespace found in {path}")


def parse_header(path: str | Path) -> DSRHeader:
    """Read the message header without loading the whole document."""
    source = period_start = period_end = currency = None
    version = "unknown"

    for event, el in etree.iterparse(
        str(path), events=("end",), recover=True
    ):
        name = _localname(el.tag)
        if name == "MessageVersion" and el.text and version == "unknown":
            version = el.text.strip()
        elif name == "MessageSender" and source is None:
            party = (
                _text(el, "FullName")
                or _text(el, "PartyName")
                or _text(el, "PartyId")
            )
            source = party or "unknown"
        elif name == "StartDate" and period_start is None:
            period_start = el.text.strip() if el.text else None
        elif name == "EndDate" and period_end is None:
            period_end = el.text.strip() if el.text else None
        elif name == "CurrencyCode" and currency is None and el.text:
            currency = el.text.strip()
        if all((source, period_start, period_end, currency)):
            break

    if not (period_start and period_end and currency):
        raise DSRError(
            f"incomplete DSR header in {path}: "
            f"start={period_start} end={period_end} currency={currency}"
        )
    return DSRHeader(
        source=source or "unknown",
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        message_version=version,
    )


def _find_deep(el, *names: str):
    """Depth-first search for the first descendant whose local name matches."""
    for child in el.iter():
        if _localname(child.tag) in names:
            return child
    return None


def parse_lines(path: str | Path, currency: str | None = None) -> list[StatementLine]:
    """Extract sales line items from a DSR message.

    Memory-safe: clears each ``SalesData``-level element as it is consumed.
    """
    lines: list[StatementLine] = []
    line_no = 0
    pending_currency = currency

    context = etree.iterparse(str(path), events=("end",), recover=True)
    for _, el in context:
        name = _localname(el.tag)
        if name != "SalesDataToRecordCompany":
            continue

        line_no += 1
        isrc = _text(el, "ISRC")
        upc = _text(el, "ICPN") or _text(el, "UPC")
        title = _text(el, "TitleText") or _text(el, "DisplayTitleText")
        artist = _text(el, "DisplayArtistName")
        dsp = _text(el, "ServiceName") or _text(el, "DSPName")
        territory = _text(el, "TerritoryCode") or _text(el, "Territory")
        qty_raw = _text(el, "Quantity") or _text(el, "Units")
        amount_raw = (
            _text(el, "NetRevenue")
            or _text(el, "Amount")
            or _text(el, "LineAmount")
        )
        cur = _text(el, "CurrencyCode") or pending_currency
        pending_currency = cur or pending_currency

        if cur is None:
            raise DSRError(f"line {line_no}: no currency for a sales line")

        gross_raw = _text(el, "GrossRevenue") or amount_raw or "0"
        ded_raw = _text(el, "Deductions") or _text(el, "Deduction") or "0"

        qty = int(Decimal(str(qty_raw or "0")))
        gross = Money.from_decimal(Decimal(str(gross_raw)), cur)
        deductions = Money.from_decimal(Decimal(str(ded_raw)), cur)
        net_raw = amount_raw if amount_raw is not None else str(
            gross.to_decimal() - deductions.to_decimal()
        )
        net = Money.from_decimal(Decimal(str(net_raw)), cur)

        lines.append(
            StatementLine(
                line_no=line_no,
                raw_isrc=isrc,
                raw_title=title,
                raw_artist=artist,
                quantity=qty,
                net=net,
                gross=gross,
                deductions=deductions,
                raw_upc=upc,
                raw_dsp=dsp,
                territory=territory,
            )
        )
        el.clear()

    if not lines:
        raise DSRError(f"no sales lines found in {path}")
    return lines

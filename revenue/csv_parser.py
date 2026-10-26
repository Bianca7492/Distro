"""CSV statement parser.

Distributor CSV exports vary in column naming and layout. This parser is
header-tolerant: it maps a set of known aliases onto the canonical fields and
fails loudly on anything it cannot account for, rather than silently dropping
revenue.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .money import Money
from .store import StatementLine

# Alias -> canonical field. Matching is case-insensitive and ignores spaces,
# underscores and punctuation, so "Net Amount" and "net_amount" both work.
ALIASES: dict[str, str] = {
    "isrc": "isrc", "isrccode": "isrc", "isrccodeisrc": "isrc",
    "upc": "upc", "icpn": "upc", "barcode": "upc",
    "title": "title", "tracktitle": "title", "songtitle": "title",
    "release title": "title", "track": "title",
    "artist": "artist", "displayartist": "artist", "artistname": "artist",
    "dsp": "dsp", "store": "dsp", "platform": "dsp", "service": "dsp",
    "territory": "territory", "country": "territory", "region": "territory",
    "quantity": "quantity", "units": "quantity", "streams": "quantity",
    "downloads": "quantity", "qty": "quantity",
    "gross": "gross", "grossamount": "gross", "grossrevenue": "gross",
    "net": "net", "netamount": "net", "netrevenue": "net",
    "earnings": "net", "royalty": "net", "amount": "net",
    "deductions": "deductions", "fees": "deductions", "commission": "deductions",
    "currency": "currency", "currencycode": "currency", "cur": "currency",
}

REQUIRED = {"title", "net"}


class CSVParserError(ValueError):
    pass


def _norm_header(h: str) -> str:
    return "".join(ch for ch in h.strip().lower() if ch.isalnum())


def map_header(fieldnames: list[str]) -> dict[str, str]:
    """Return canonical-field -> actual-column-name."""
    mapping: dict[str, str] = {}
    for col in fieldnames:
        canon = ALIASES.get(_norm_header(col))
        if canon and canon not in mapping:
            mapping[canon] = col
    missing = REQUIRED - mapping.keys()
    if missing:
        raise CSVParserError(
            f"CSV is missing required column(s): {sorted(missing)}; "
            f"saw {fieldnames}"
        )
    return mapping


def _money(raw: str | None, currency: str) -> Money:
    if raw is None or str(raw).strip() == "":
        return Money(0, currency)
    cleaned = (
        str(raw).strip()
        .replace(",", "")
        .replace("$", "")
        .replace("£", "")
        .replace("€", "")
    )
    if cleaned in ("", "-", "—"):
        return Money(0, currency)
    try:
        return Money.from_decimal(Decimal(cleaned), currency)
    except (InvalidOperation, ValueError) as exc:
        raise CSVParserError(f"cannot parse amount {raw!r}: {exc}") from exc


def parse_lines(
    path: str | Path, default_currency: str = "USD"
) -> list[StatementLine]:
    lines: list[StatementLine] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise CSVParserError(f"empty CSV: {path}")
        mapping = map_header(reader.fieldnames)

        for i, row in enumerate(reader, start=1):
            currency = (
                row.get(mapping.get("currency", ""), default_currency)
                or default_currency
            ).strip().upper()
            if len(currency) != 3:
                currency = default_currency

            net = _money(row.get(mapping["net"]), currency)
            gross = (
                _money(row.get(mapping["gross"]), currency)
                if "gross" in mapping
                else net
            )
            deductions = (
                _money(row.get(mapping["deductions"]), currency)
                if "deductions" in mapping
                else Money(0, currency)
            )
            qty_raw = row.get(mapping.get("quantity", ""), "0") or "0"
            try:
                qty = int(Decimal(str(qty_raw).replace(",", "").strip() or "0"))
            except (InvalidOperation, ValueError):
                qty = 0

            lines.append(
                StatementLine(
                    line_no=i,
                    raw_isrc=(row.get(mapping.get("isrc", "")) or None),
                    raw_title=(row.get(mapping["title"]) or None),
                    raw_artist=(row.get(mapping.get("artist", "")) or None),
                    quantity=qty,
                    net=net,
                    gross=gross,
                    deductions=deductions,
                    raw_upc=(row.get(mapping.get("upc", "")) or None),
                    raw_dsp=(row.get(mapping.get("dsp", "")) or None),
                    territory=(row.get(mapping.get("territory", "")) or None),
                )
            )

    if not lines:
        raise CSVParserError(f"no data rows in {path}")
    return lines

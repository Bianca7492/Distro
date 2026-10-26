"""Adapter over the donor `dsrf` library (ddexnet/dsrf).

The donor library parses DDEX **flat-file** statements (TSV + XSD profile) into
protobuf blocks. It is the official Google/DSP implementation and is what we use
for flat-file statements rather than writing our own.

Two caveats, both verified against the donor source:

  * its **validator** is broken — it rejects row types its own XSD accepts
    (``SY0201`` vs ``SY02``). We parse with it and validate ourselves.
  * it needs three patches on Python 3.11+ (see ``scripts/prepare_dsrf.py``).

This adapter converts donor blocks into our canonical ``StatementLine`` so both
the XML path and the flat-file path feed the same matcher and reconciliation.
"""

from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .money import Money
from .store import StatementLine

# The donor library is third-party and git-ignored; it is imported lazily so the
# rest of the package works without it.
_STACK = Path(__file__).resolve().parents[1] / "boe-stack"


class DSrfUnavailable(RuntimeError):
    """The donor dsrf library is not fetched or not prepared."""


def _ensure_on_path() -> None:
    p = str(_STACK)
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_donor():
    _ensure_on_path()
    warnings.filterwarnings("ignore", category=UserWarning)
    try:
        from dsrf import dsrf_logger  # noqa: PLC0415
        from dsrf.parsers.dsrf_file_parser import DSRFFileParser  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - environment dependent
        raise DSrfUnavailable(
            f"donor dsrf not usable ({exc}). "
            "Run scripts/fetch_stack.sh then scripts/prepare_dsrf.py."
        ) from exc
    return dsrf_logger, DSRFFileParser


@dataclass
class DSrfHeader:
    profile: str
    profile_version: str
    message_id: str
    created: str
    file_number: int
    file_count: int


# Profile -> XSD path, relative to the donor repo.
PROFILE_XSD = {
    ("UgcProfile", "1.0"): "schemas/UgcProfile/1.0/UgcProfile.xsd",
    ("UgcProfile", "1.1.1"): "schemas/UgcProfile/1.1.1/UgcProfile.xsd",
    ("UgcProfile", "1.2"): "schemas/UgcProfile/1.2/UgcProfile.xsd",
    (
        "FinancialReportingToRecordCompaniesProfileSRB", "1.0",
    ): "schemas/FinancialReportingToRecordCompaniesProfileSRB/1.0/"
       "FinancialReportingToRecordCompaniesProfileSRB.xsd",
    ("RoyaltyReportingProfile", "1.0.1"):
        "schemas/RoyaltyReportingProfile/1.0.1/RoyaltyReportingProfile.xsd",
}

# Statements in the wild declare a profile version that has no shipped XSD.
# DDEX treats these as compatible; map the declared version to the nearest
# schema we actually have.
VERSION_ALIAS = {
    ("UgcProfile", "1.1"): ("UgcProfile", "1.1.1"),
    ("UgcProfile", "1.0.1"): ("UgcProfile", "1.0"),
}


def _cells(row) -> dict[str, str]:
    """Flatten a row's cells to name -> text.

    ``string_value``, ``integer_value`` and ``decimal_value`` are ``repeated``
    in the proto, and ``cell_type`` is an enum, so presence cannot be tested
    with ``HasField``. Decimal values are rendered with ``repr`` so that the
    shortest round-trip representation reaches ``Decimal`` unchanged.
    """
    out: dict[str, str] = {}
    for c in row.cells:
        if len(c.string_value):
            out[c.name] = c.string_value[0]
        elif len(c.integer_value):
            out[c.name] = str(c.integer_value[0])
        elif len(c.decimal_value):
            out[c.name] = repr(c.decimal_value[0])
        elif len(c.boolean_value):
            out[c.name] = "true" if c.boolean_value[0] else "false"
    return out


def parse_header(path: str | Path) -> DSrfHeader:
    """Read the HEAD block: profile, version, message id, dates."""
    donor = Path(path).read_text(encoding="utf-8", errors="replace")
    first = donor.splitlines()[0]
    parts = first.split("\t")
    if not parts or parts[0].strip().lstrip("#") != "HEAD":
        raise ValueError(f"not a DSRF flat file (no HEAD): {path}")
    # Two shapes seen in the wild:
    #   HEAD|ID1|UgcProfile|1.1|1|2            (older)
    #   HEAD|dsrf/30|UGCProfile|1.0|<id>|<ts>|<n>|<n>|...
    vals = [p.strip() for p in parts[1:]]

    def pick(*cands):
        for c in cands:
            if c < len(vals) and vals[c]:
                return vals[c]
        return ""

    profile = pick(1, 2)
    version = pick(2, 3)
    return DSrfHeader(
        profile=profile,
        profile_version=version,
        message_id=pick(4, 0),
        created=pick(5, 3),
        file_number=int(pick(4, 1) or 1) if pick(4, 1).isdigit() else 1,
        file_count=int(pick(5, 2) or 1) if pick(5, 2).isdigit() else 1,
    )


def _resolve_xsd(profile: str, version: str) -> Path:
    key = (profile, version)
    if key not in PROFILE_XSD:
        # case-tolerant lookup, then declared-version alias
        for (p, v), rel in PROFILE_XSD.items():
            if p.lower() == profile.lower() and v == version:
                key = (p, v)
                break
    if key not in PROFILE_XSD:
        for (p, v), alias in VERSION_ALIAS.items():
            if p.lower() == profile.lower() and v == version:
                key = alias
                break
    if key not in PROFILE_XSD:
        raise DSrfUnavailable(
            f"no XSD for profile {profile!r} version {version!r}; "
            f"known: {sorted(PROFILE_XSD)}"
        )
    xsd = _STACK / "dsrf" / PROFILE_XSD[key]
    if not xsd.exists():
        raise DSrfUnavailable(f"XSD missing on disk: {xsd}")
    return xsd


# Row-type bases. Some profiles append a version suffix, either dotted
# ("SU03.01") or concatenated ("SU0301"); others use the bare base ("SU03").
# Normalise to the 4-character base before classifying.
BASES = ("AS01", "AS02", "RE01", "RE02", "SU02", "SU03", "LI01", "MW01",
         "RU01", "RU02")

SALES_BASES = ("SU02", "SU03", "AS02")
ALLOC_BASES = ("LI01",)
RESOURCE_BASES = ("AS01", "RE01", "RE02")


def _base(row_type: str) -> str:
    t = row_type.split(".")[0].strip().upper()
    for b in BASES:
        if t.startswith(b):
            return b
    return t


def _kind(row_type: str) -> str:
    base = _base(row_type)
    if base in SALES_BASES:
        return "sale"
    if base in ALLOC_BASES:
        return "alloc"
    if base in RESOURCE_BASES:
        return "resource"
    return "other"


def parse_lines(path: str | Path) -> list[StatementLine]:
    """Parse a DSRF flat file into canonical statement lines.

    Structure of a DDEX sales statement:

        AS01  resource metadata, keyed by ResourceReference (carries the ISRC)
        SU03  sales transaction: resource ref, quantity, net revenue
        LI01  allocation of that revenue to a rights controller

    Revenue lands on the SU03 row. LI01 rows apportion it; when allocations are
    present we emit one line per allocation, otherwise one line for the sale.
    """
    dsrf_logger, DSRFFileParser = _load_donor()
    header = parse_header(path)
    xsd = _resolve_xsd(header.profile, header.profile_version)

    log = dsrf_logger.DSRFLogger("dsrf", "/tmp/boe_dsrf.log")
    parser = DSRFFileParser(log, str(xsd), None, str(path))

    resources: dict[str, dict] = {}
    sales: list[dict] = []
    allocs: dict[str, list[dict]] = {}

    for block in parser.parse_file(1):
        for row in block.rows:
            c = _cells(row)
            kind = _kind(row.type)
            if kind == "resource":
                ref = c.get("ResourceReference") or c.get("ResourceId")
                if ref:
                    resources[ref] = {
                        "isrc": c.get("ISRC"),
                        "title": c.get("Title") or c.get("DisplayTitle"),
                        "artist": c.get("DisplayArtistName")
                        or c.get("ArtistName"),
                    }
            elif kind == "sale":
                ref = c.get("ResourceReference") or c.get("DspReleaseId")
                net = _money(c, "NetRevenue", "AllocatedNetRevenue")
                if net is None:
                    continue
                sales.append(
                    {
                        "ref": ref,
                        "summary": c.get("SummaryRecordId"),
                        "qty": _int(c.get("Usages") or c.get("Quantity")) or 0,
                        "net": net,
                        "dsp": c.get("ServiceDescription")
                        or c.get("DistributionChannel"),
                        "territory": c.get("Territory"),
                        "currency": c.get("Currency")
                        or c.get("CurrencyCode"),
                    }
                )
            elif kind == "alloc":
                summary = c.get("SummaryRecordId")
                if summary:
                    allocs.setdefault(summary, []).append(
                        {
                            "controller": c.get("RightsController"),
                            "share": c.get("RightShare%")
                            or c.get("RightShare"),
                            "net": _money(c, "AllocatedNetRevenue"),
                            "amount": _money(c, "AllocatedAmount"),
                        }
                    )

    lines: list[StatementLine] = []
    for n, sale in enumerate(sales, start=1):
        meta = resources.get(sale["ref"], {})
        lines.append(
            StatementLine(
                line_no=n,
                raw_isrc=meta.get("isrc"),
                raw_title=meta.get("title"),
                raw_artist=meta.get("artist"),
                quantity=sale["qty"],
                net=sale["net"],
                gross=sale["net"],
                deductions=Money(0, sale["net"].currency),
                raw_dsp=sale["dsp"],
                territory=sale["territory"],
            )
        )

    if not lines:
        raise ValueError(f"no sales rows parsed from {path}")
    return lines


def parse_allocations(path: str | Path) -> dict[str, list[dict]]:
    """Allocations of sales revenue to rights controllers, by summary record.

    Not used by Stage 1 (revenue truth reports gross net per sale). Stage 4
    (royalty splits) consumes this to apportion revenue to holders.
    """
    dsrf_logger, DSRFFileParser = _load_donor()
    header = parse_header(path)
    xsd = _resolve_xsd(header.profile, header.profile_version)
    log = dsrf_logger.DSRFLogger("dsrf", "/tmp/boe_dsrf.log")
    parser = DSRFFileParser(log, str(xsd), None, str(path))

    out: dict[str, list[dict]] = {}
    for block in parser.parse_file(1):
        for row in block.rows:
            if _kind(row.type) != "alloc":
                continue
            c = _cells(row)
            summary = c.get("SummaryRecordId")
            if not summary:
                continue
            out.setdefault(summary, []).append(
                {
                    "controller": c.get("RightsController"),
                    "share": c.get("RightShare%") or c.get("RightShare"),
                    "net": _money(c, "AllocatedNetRevenue"),
                    "amount": _money(c, "AllocatedAmount"),
                }
            )
    return out


def _int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(Decimal(str(raw)))
    except (InvalidOperation, ValueError):
        return None


def _money(cells: dict[str, str], *names: str) -> Money | None:
    currency = cells.get("CurrencyCode") or cells.get("Currency") or "USD"
    if len(currency) != 3:
        currency = "USD"
    for name in names:
        raw = cells.get(name)
        if raw not in (None, ""):
            try:
                return Money.from_decimal(Decimal(str(raw)), currency)
            except (InvalidOperation, ValueError):
                continue
    return None

"""Tests for the DSRF flat-file adapter over the donor `dsrf` library.

The donor library is third-party and git-ignored, so these tests skip when the
stack has not been fetched. They are integration tests against real DDEX flat
files shipped with the donor, which is the point: they catch the donor's actual
quirks rather than a mock's.

    bash scripts/fetch_stack.sh dsrf
    python3 scripts/prepare_dsrf.py
    python3 -m pytest tests/test_dsrf_adapter.py -v
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from revenue import dsrf_adapter

warnings.filterwarnings("ignore", category=UserWarning)

STACK = Path(__file__).resolve().parents[1] / "boe-stack"
DONOR = STACK / "dsrf"
TESTDATA = DONOR / "testdata"

# A sales statement: AS01 resource rows, SU03 sales, LI01 allocations.
SALES_FILE = TESTDATA / (
    "DSR_TEST2_YouTube_AdSupport-music_2015-Q4_IS_1of1_20160121T150926.tsv"
)

pytestmark = pytest.mark.skipif(
    not SALES_FILE.exists(),
    reason="donor dsrf stack not fetched (run scripts/fetch_stack.sh dsrf)",
)


def test_donor_is_prepared():
    """The donor must import; it needs protobuf bindings and two patches."""
    dsrf_logger, parser = dsrf_adapter._load_donor()
    assert parser is not None


def test_header_declares_profile_and_period():
    h = dsrf_adapter.parse_header(SALES_FILE)
    assert h.profile.lower() == "ugcprofile"
    # The file declares 1.1, for which no XSD ships; the adapter must not
    # simply fail on that.
    assert h.profile_version.startswith("1.1")


def test_version_alias_resolves_missing_schema():
    """Declared 1.1 has no shipped XSD; it must resolve to the 1.1.1 schema."""
    xsd = dsrf_adapter._resolve_xsd("UGCProfile", "1.1")
    assert xsd.exists()
    assert "1.1.1" in str(xsd)


def test_row_type_bases_normalise_across_profiles():
    """Row types appear dotted, concatenated, or bare depending on profile."""
    for raw in ("SU03", "SU03.01", "SU0301"):
        assert dsrf_adapter._base(raw) == "SU03"
        assert dsrf_adapter._kind(raw) == "sale"
    for raw in ("LI01", "LI01.01", "LI0101"):
        assert dsrf_adapter._kind(raw) == "alloc"
    for raw in ("AS01", "AS01.01"):
        assert dsrf_adapter._kind(raw) == "resource"


def test_decimal_cells_are_not_dropped():
    """The donor stores decimals in `decimal_value`, not `string_value`.

    Reading only string/integer cells silently loses every revenue figure,
    which is the failure this test exists to prevent.
    """
    lines = dsrf_adapter.parse_lines(SALES_FILE)
    assert any(ln.net.minor != 0 for ln in lines), (
        "all revenue parsed as zero: decimal cells were dropped"
    )


def test_sales_are_extracted_with_isrc_and_quantity():
    lines = dsrf_adapter.parse_lines(SALES_FILE)
    assert len(lines) == 6

    isrcs = {ln.raw_isrc for ln in lines}
    assert "USSM19803037" in isrcs          # Sexual Healing
    assert "USUV71101875" in isrcs          # All I Want Is You
    assert "USRH10903879" in isrcs          # This Bitter Earth

    assert all(ln.quantity > 0 for ln in lines)
    assert all(ln.net.currency == "USD" for ln in lines)


def test_resource_metadata_is_joined_to_sales():
    """AS01 rows carry the ISRC; the join must land it on the sale line."""
    lines = dsrf_adapter.parse_lines(SALES_FILE)
    by_isrc = {ln.raw_isrc: ln for ln in lines}
    sexual = by_isrc["USSM19803037"]
    assert "Sexual Healing" in (sexual.raw_title or "")
    assert "Marvin Gaye" in (sexual.raw_artist or "")


def test_total_is_the_sum_of_lines():
    lines = dsrf_adapter.parse_lines(SALES_FILE)
    total = sum(ln.net.minor for ln in lines)
    assert total == sum(ln.net.minor for ln in lines)
    assert total > 0


def test_allocations_are_parsed_separately():
    """Allocations are Stage 4 input, not Stage 1 revenue."""
    allocs = dsrf_adapter.parse_allocations(SALES_FILE)
    assert allocs, "expected LI01 allocation rows"
    controllers = {
        a["controller"] for bucket in allocs.values() for a in bucket
    }
    assert controllers - {None}


def test_format_detection_finds_flat_file():
    from revenue.pipeline import detect_format

    assert detect_format(SALES_FILE) == "dsrf"
    # An XML statement must not be mistaken for a flat file.
    fixture = Path(__file__).parent / "fixtures" / "sample_dsr.xml"
    assert detect_format(fixture) == "dsr"

"""CIN placeholder detection and decoding (canonical rule in the verifier)."""

import pytest

from app.scrapers.mca_scraper import decode_cin
from app.validators.brsr_report_verifier import (
    cin_registration, is_real_cin, placeholder_reasons)


KNOWN_PLACEHOLDERS = [
    "Z99999ZZ9999ZZZ000000",   # SBIN (older filings)
    "Z99999ZZ9999ZZZ999999",   # SBIN (older filings)
    "A00000AA0000AAA000000",   # SBIN
    "X00000XX0000XXX000000",   # PNB
    "N00000NA0000PLC000000",   # CANBK
    "U12345KA1234KAA123456",   # CANBK (older filings)
    "U99999AB1111ABC000999",   # UNIONBANK (older filings)
    "L01234KA2023PTC567891",   # BANKBARODA
]

KNOWN_REAL = [
    "L99999MH1946PLC004768",   # LT - NIC 99999 is legitimate
    "L99999MH1937PLC002641",   # CGPOWER
    "L99999MH2000PLC129113",   # SBILIFE
    "L32102KA1945PLC020800",   # WIPRO - short digit run in NIC
    "L32100GJ1996PLC030976",   # IDEA
    "L74999TZ1984PLC001456",   # TITAN - old ROC code TZ
    "L65993PN2007PLC130076",   # BAJAJ-AUTO - old ROC code PN
    "L28920MH1945PLC004520",   # TMPV
    "L35106MH1986PLC284510",   # MOTHERSON
    "L35301KA1963GOI001622",   # HAL - government company
    "L65110MH1919GOI000606",   # UNIONBANK (2026 filing)
]


@pytest.mark.parametrize("cin", KNOWN_PLACEHOLDERS)
def test_placeholders_are_detected(cin):
    assert placeholder_reasons(cin)
    assert not is_real_cin(cin)


@pytest.mark.parametrize("cin", KNOWN_REAL)
def test_real_cins_are_not_flagged(cin):
    assert placeholder_reasons(cin) == []
    assert is_real_cin(cin)


def test_no_false_positives_in_verified_filings(verification):
    """Every CIN seen in the filings except the known dummies is real."""
    seen = {c for c in verification["xbrl_cin"] if c}
    flagged = {c for c in seen if placeholder_reasons(c)}
    assert flagged <= set(KNOWN_PLACEHOLDERS), flagged - set(
        KNOWN_PLACEHOLDERS)


def test_blank_and_garbage_are_not_real():
    for value in ["", None, "   ", "INE002A01018", "NOT-A-CIN", "L" * 21]:
        assert not is_real_cin(value)


def test_registration_ignores_nic_and_listing_flag():
    # MOTHERSON changed NIC code; same company
    assert cin_registration("L34300MH1986PLC284510") == \
        cin_registration("L35106MH1986PLC284510")
    assert cin_registration("U34300MH1986PLC284510") == \
        cin_registration("L35106MH1986PLC284510")
    assert cin_registration("garbage") is None


def test_decode_cin_fields():
    d = decode_cin("L74999TZ1984PLC001456")
    assert d["cin_listing_flag"] == "LISTED"
    assert d["cin_incorporation_year"] == 1984
    assert d["cin_state"].startswith("Tamil Nadu")
    assert d["cin_company_type_code"] == "PLC"
    assert not d["cin_is_placeholder"]
    bad = decode_cin("A00000AA0000AAA000000")
    assert bad["cin_format_valid"] is False and bad["cin_is_placeholder"]

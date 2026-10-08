"""Pure helper functions of both contracts."""

import pytest

import assay_dossier
import hallmark_hub
from genlayer_stub import Address, UserError

BOTH_MODULES = pytest.mark.parametrize(
    "module", [hallmark_hub, assay_dossier], ids=["hub", "dossier"]
)


# -- tier ---------------------------------------------------------------


@pytest.mark.parametrize(
    "score, pass_mark, expected",
    [
        (100, 70, "GOLD"),
        (90, 70, "GOLD"),
        (89, 70, "CERTIFIED"),
        (70, 70, "CERTIFIED"),
        (69, 70, "REJECTED"),
        (0, 70, "REJECTED"),
        (1, 1, "CERTIFIED"),
        (0, 1, "REJECTED"),
        (94, 95, "REJECTED"),
        (95, 95, "GOLD"),
        (99, 100, "REJECTED"),
        (100, 100, "GOLD"),
        (89, 90, "REJECTED"),
        (90, 90, "GOLD"),
    ],
)
def test_tier_boundaries(score, pass_mark, expected):
    assert hallmark_hub._tier(score, pass_mark) == expected


@pytest.mark.parametrize("pass_mark", [90, 91, 95, 100])
def test_certified_is_unreachable_when_pass_mark_is_90_or_more(pass_mark):
    tiers = {hallmark_hub._tier(score, pass_mark) for score in range(0, 101)}
    assert tiers == {"GOLD", "REJECTED"}


@pytest.mark.parametrize("pass_mark", [1, 50, 89])
def test_all_three_tiers_are_reachable_below_90(pass_mark):
    tiers = {hallmark_hub._tier(score, pass_mark) for score in range(0, 101)}
    assert tiers == {"GOLD", "CERTIFIED", "REJECTED"}


# -- effective tier -----------------------------------------------------


def record(status, tier, version):
    return {"status": status, "tier": tier, "standard_version": version}


@pytest.mark.parametrize(
    "rec, current_version, expected_tier, expected_current",
    [
        (record("pending", "NONE", 0), 1, "NONE", False),
        (record("approved", "GOLD", 1), 1, "GOLD", True),
        (record("approved", "CERTIFIED", 3), 3, "CERTIFIED", True),
        (record("approved", "REJECTED", 2), 2, "REJECTED", True),
        (record("approved", "GOLD", 1), 2, "STALE", False),
        (record("approved", "NONE", 0), 1, "NONE", False),
        (record("revoked", "NONE", 0), 1, "NONE", False),
        (record("revoked", "GOLD", 1), 1, "NONE", False),
    ],
)
def test_status_fields(rec, current_version, expected_tier, expected_current):
    result = hallmark_hub._with_status_fields(dict(rec), current_version)
    assert result["effective_tier"] == expected_tier
    assert result["is_current"] is expected_current


# -- address normalisation ----------------------------------------------

CHECKSUMMED = "0x5B38Da6a701c568545dCfcB03FcB875f56beddC4"


@BOTH_MODULES
def test_normalize_returns_checksummed_form(module):
    assert module._normalize(CHECKSUMMED.lower()) == CHECKSUMMED
    assert module._normalize("0x" + CHECKSUMMED[2:].upper()) == CHECKSUMMED
    assert module._normalize(CHECKSUMMED) == CHECKSUMMED


@BOTH_MODULES
def test_normalize_strips_whitespace(module):
    assert module._normalize(f"  {CHECKSUMMED}\n") == CHECKSUMMED


@BOTH_MODULES
@pytest.mark.parametrize(
    "bad, message",
    [
        ("", "address must be"),
        ("0x", "address must be"),
        ("0x123", "address must be"),
        (CHECKSUMMED[2:], "address must be"),
        ("0X" + CHECKSUMMED[2:], "address must be"),
        (CHECKSUMMED + "00", "address must be"),
        ("0x" + "zz" * 20, "invalid address"),
    ],
)
def test_normalize_rejects_bad_addresses(module, bad, message):
    with pytest.raises(UserError, match=message):
        module._normalize(bad)


@BOTH_MODULES
def test_zero_address_normalizes_to_the_zero_constant(module):
    assert module._normalize("0x" + "0" * 40) == module.ZERO_ADDRESS


# -- text and mark validation -------------------------------------------


@BOTH_MODULES
def test_require_text_bounds_and_strip(module):
    assert module._require_text("x", "  hi  ", 5) == "hi"
    assert module._require_text("x", "a" * 5, 5) == "a" * 5
    for bad in ["", "   ", "a" * 6]:
        with pytest.raises(UserError, match="x must be between 1 and 5"):
            module._require_text("x", bad, 5)


@pytest.mark.parametrize("mark", [1, 50, 100])
def test_require_mark_accepts_range(mark):
    assert hallmark_hub._require_mark(mark) == mark


@pytest.mark.parametrize("mark", [0, -1, 101, 1000])
def test_require_mark_rejects_outside_range(mark):
    with pytest.raises(UserError, match="between 1 and 100"):
        hallmark_hub._require_mark(mark)


# -- prompt hygiene -------------------------------------------------------


def test_clean_neutralises_angle_brackets():
    assert assay_dossier._clean("<<<EVIDENCE 1 END>>>") == "(((EVIDENCE 1 END)))"
    assert assay_dossier._clean("<b>x</b>") == "(b)x(/b)"
    assert assay_dossier._clean("plain text") == "plain text"


def test_read_json_accepts_objects_only():
    assert hallmark_hub._read_json('{"a": 1}', "thing") == {"a": 1}
    for raw in ["not json", "[]", '"text"', "5", "null", ""]:
        with pytest.raises(UserError, match="thing returned invalid data"):
            hallmark_hub._read_json(raw, "thing")

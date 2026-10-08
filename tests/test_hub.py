"""HallmarkHub: standard, ownership, registration, attestation and views."""

import json

import pytest

import hallmark_hub
from genlayer_stub import UserError
from harness import EVIDENCE_URL, FakeSatellite, account, audit_with, hex_of, verdict_json

ZERO = "0x" + "0" * 40


def lower(address_hex):
    return "0x" + address_hex[2:].lower()


def get_record(chain, hub, dossier, viewer):
    return chain.view(hub, viewer, "get_dossier", hex_of(dossier))


def register(chain, hub, dossier, applicant, approver=None):
    """apply (and optionally approve) a dossier."""
    chain.call(hub, applicant, "apply", hex_of(dossier))
    if approver is not None:
        chain.call(hub, approver, "approve", hex_of(dossier))


# ----------------------------------------------------------------------
# constructor and standard
# ----------------------------------------------------------------------


def test_constructor_stores_standard_and_owner(chain, hub, owner):
    assert chain.view(hub, owner, "get_standard") == {
        "hub": hex_of(hub),
        "registry_name": "Test Registry",
        "title": "Quality Standard v1",
        "criteria": "Be good. Show evidence.",
        "pass_mark": 70,
        "version": 1,
    }
    summary = chain.view(hub, owner, "get_summary")
    assert summary == {
        "registry_name": "Test Registry",
        "owner": owner.as_hex,
        "pending_owner": "",
        "standard_version": 1,
        "pass_mark": 70,
        "total_dossiers": 0,
    }


def test_constructor_strips_whitespace(chain, owner):
    hub = chain.deploy(hallmark_hub.HallmarkHub, owner, "  R  ", " T ", " C ", 50)
    std = chain.view(hub, owner, "get_standard")
    assert (std["registry_name"], std["title"], std["criteria"]) == ("R", "T", "C")


@pytest.mark.parametrize(
    "args, message",
    [
        (("", "t", "c", 70), "registry_name must be between 1 and 80"),
        (("   ", "t", "c", 70), "registry_name must be between 1 and 80"),
        (("r" * 81, "t", "c", 70), "registry_name must be between 1 and 80"),
        (("r", "", "c", 70), "standard_title must be between 1 and 120"),
        (("r", "t" * 121, "c", 70), "standard_title must be between 1 and 120"),
        (("r", "t", "", 70), "standard_criteria must be between 1 and 3000"),
        (("r", "t", "c" * 3001, 70), "standard_criteria must be between 1 and 3000"),
        (("r", "t", "c", 0), "pass_mark must be between 1 and 100"),
        (("r", "t", "c", 101), "pass_mark must be between 1 and 100"),
        (("r", "t", "c", -5), "pass_mark must be between 1 and 100"),
    ],
)
def test_constructor_rejects_bad_arguments(chain, owner, args, message):
    with pytest.raises(UserError, match=message):
        chain.deploy(hallmark_hub.HallmarkHub, owner, *args)


@pytest.mark.parametrize("pass_mark", [1, 100])
def test_constructor_accepts_pass_mark_limits(chain, owner, pass_mark):
    hub = chain.deploy(hallmark_hub.HallmarkHub, owner, "r", "t", "c", pass_mark)
    assert chain.view(hub, owner, "get_standard")["pass_mark"] == pass_mark


def test_constructor_accepts_maximum_lengths(chain, owner):
    hub = chain.deploy(
        hallmark_hub.HallmarkHub, owner, "r" * 80, "t" * 120, "c" * 3000, 70
    )
    assert chain.view(hub, owner, "get_standard")["version"] == 1


def test_get_standard_reports_the_hub_own_address(chain, hub, alice):
    # Satellites compare this address with the hub they were deployed for.
    assert chain.view(hub, alice, "get_standard")["hub"] == hex_of(hub)


# ----------------------------------------------------------------------
# update_standard
# ----------------------------------------------------------------------


def test_update_standard_bumps_version_and_replaces_fields(chain, hub, owner):
    chain.call(hub, owner, "update_standard", "Standard v2", "New criteria", 85)
    assert chain.view(hub, owner, "get_standard") == {
        "hub": hex_of(hub),
        "registry_name": "Test Registry",
        "title": "Standard v2",
        "criteria": "New criteria",
        "pass_mark": 85,
        "version": 2,
    }
    chain.call(hub, owner, "update_standard", "Standard v3", "Newer", 60)
    assert chain.view(hub, owner, "get_summary")["standard_version"] == 3


def test_update_standard_is_owner_only(chain, hub, alice):
    with pytest.raises(UserError, match="only the hub owner"):
        chain.call(hub, alice, "update_standard", "t", "c", 50)
    assert chain.view(hub, alice, "get_standard")["version"] == 1


@pytest.mark.parametrize(
    "args, message",
    [
        (("", "c", 50), "standard_title"),
        (("t", "  ", 50), "standard_criteria"),
        (("t" * 121, "c", 50), "standard_title"),
        (("t", "c" * 3001, 50), "standard_criteria"),
        (("t", "c", 0), "pass_mark"),
        (("t", "c", 101), "pass_mark"),
    ],
)
def test_update_standard_validates_and_changes_nothing(chain, hub, owner, args, message):
    with pytest.raises(UserError, match=message):
        chain.call(hub, owner, "update_standard", *args)
    std = chain.view(hub, owner, "get_standard")
    assert std["version"] == 1 and std["title"] == "Quality Standard v1"
    assert std["pass_mark"] == 70


# ----------------------------------------------------------------------
# ownership
# ----------------------------------------------------------------------


def test_two_step_ownership_transfer(chain, hub, owner, alice, bob):
    chain.call(hub, owner, "propose_owner", alice.as_hex)
    assert chain.view(hub, bob, "get_summary")["pending_owner"] == alice.as_hex
    # still the old owner until the new one accepts
    assert chain.view(hub, bob, "get_summary")["owner"] == owner.as_hex
    chain.call(hub, alice, "accept_ownership")
    summary = chain.view(hub, bob, "get_summary")
    assert summary["owner"] == alice.as_hex and summary["pending_owner"] == ""
    # rights moved with it
    with pytest.raises(UserError, match="only the hub owner"):
        chain.call(hub, owner, "update_standard", "t", "c", 50)
    chain.call(hub, alice, "update_standard", "t", "c", 50)


def test_propose_owner_normalizes_the_address(chain, hub, owner, alice):
    chain.call(hub, owner, "propose_owner", lower(alice.as_hex))
    assert chain.view(hub, owner, "get_summary")["pending_owner"] == alice.as_hex
    chain.call(hub, alice, "accept_ownership")


def test_propose_owner_rules(chain, hub, owner, alice):
    with pytest.raises(UserError, match="only the hub owner"):
        chain.call(hub, alice, "propose_owner", alice.as_hex)
    with pytest.raises(UserError, match="zero address"):
        chain.call(hub, owner, "propose_owner", ZERO)
    with pytest.raises(UserError, match="address must be"):
        chain.call(hub, owner, "propose_owner", "0x1234")


def test_accept_ownership_rules(chain, hub, owner, alice, bob):
    with pytest.raises(UserError, match="no ownership transfer is pending"):
        chain.call(hub, alice, "accept_ownership")
    chain.call(hub, owner, "propose_owner", alice.as_hex)
    with pytest.raises(UserError, match="only the proposed owner"):
        chain.call(hub, bob, "accept_ownership")
    with pytest.raises(UserError, match="only the proposed owner"):
        chain.call(hub, owner, "accept_ownership")


def test_cancel_ownership_proposal(chain, hub, owner, alice, bob):
    chain.call(hub, owner, "propose_owner", alice.as_hex)
    with pytest.raises(UserError, match="only the hub owner"):
        chain.call(hub, bob, "cancel_ownership_proposal")
    chain.call(hub, owner, "cancel_ownership_proposal")
    assert chain.view(hub, owner, "get_summary")["pending_owner"] == ""
    with pytest.raises(UserError, match="no ownership transfer is pending"):
        chain.call(hub, alice, "accept_ownership")


def test_a_new_proposal_replaces_the_previous_one(chain, hub, owner, alice, bob):
    chain.call(hub, owner, "propose_owner", alice.as_hex)
    chain.call(hub, owner, "propose_owner", bob.as_hex)
    with pytest.raises(UserError, match="only the proposed owner"):
        chain.call(hub, alice, "accept_ownership")
    chain.call(hub, bob, "accept_ownership")


# ----------------------------------------------------------------------
# apply
# ----------------------------------------------------------------------


def test_apply_registers_a_pending_dossier(chain, hub, dossier, alice, bob):
    chain.call(hub, alice, "apply", hex_of(dossier))
    assert get_record(chain, hub, dossier, bob) == {
        "dossier": hex_of(dossier),
        "applicant": alice.as_hex,
        "subject": "Acme Coffee",
        "status": "pending",
        "score": -1,
        "tier": "NONE",
        "standard_version": 0,
        "audit_count": 0,
        "attestations": 0,
        "is_current": False,
        "effective_tier": "NONE",
    }
    assert chain.view(hub, bob, "get_summary")["total_dossiers"] == 1


def test_apply_accepts_a_lowercase_address_and_detects_duplicates(
    chain, hub, dossier, alice
):
    chain.call(hub, alice, "apply", lower(hex_of(dossier)))
    assert get_record(chain, hub, dossier, alice)["dossier"] == hex_of(dossier)
    for variant in [hex_of(dossier), lower(hex_of(dossier))]:
        with pytest.raises(UserError, match="already registered"):
            chain.call(hub, alice, "apply", variant)
    assert chain.view(hub, alice, "get_summary")["total_dossiers"] == 1
    # lookups work with either spelling too
    assert chain.view(hub, alice, "get_dossier", lower(hex_of(dossier)))["status"] == "pending"


def test_apply_is_for_the_dossier_owner_only(chain, hub, dossier, bob):
    with pytest.raises(UserError, match="only the dossier owner"):
        chain.call(hub, bob, "apply", hex_of(dossier))
    assert chain.view(hub, bob, "get_summary")["total_dossiers"] == 0


def test_apply_rejects_a_dossier_linked_to_another_hub(
    chain, hub, make_dossier, alice, owner
):
    other_hub = chain.deploy(hallmark_hub.HallmarkHub, owner, "R2", "S2", "C2", 50)
    foreign = make_dossier(alice, on=other_hub)
    with pytest.raises(UserError, match="different hub"):
        chain.call(hub, alice, "apply", hex_of(foreign))


def test_apply_rejects_addresses_that_are_not_dossiers(chain, hub, alice):
    with pytest.raises(UserError, match="could not read get_link"):
        chain.call(hub, alice, "apply", account(999_999).as_hex)  # no contract
    with pytest.raises(UserError, match="could not read get_link"):
        chain.call(hub, alice, "apply", hex_of(hub))  # a contract without get_link


@pytest.mark.parametrize(
    "bad, message",
    [
        ("", "address must be"),
        ("0x123", "address must be"),
        ("not an address", "address must be"),
        ("0X" + "1" * 40, "address must be"),
        ("0x" + "zz" * 20, "invalid address"),
    ],
)
def test_apply_rejects_malformed_addresses(chain, hub, alice, bad, message):
    with pytest.raises(UserError, match=message):
        chain.call(hub, alice, "apply", bad)


def fake_link(hub, owner_address, **overrides):
    link = {"hub": hex_of(hub), "owner": owner_address.as_hex, "subject": "Fake"}
    link.update(overrides)
    return json.dumps(link)


@pytest.mark.parametrize(
    "link_json, message",
    [
        ("not json", "invalid data"),
        ("[]", "invalid data"),
        ('"text"', "invalid data"),
        ("{}", "address must be"),  # no hub field at all
    ],
)
def test_apply_rejects_malformed_get_link(chain, hub, alice, link_json, message):
    fake = chain.deploy(FakeSatellite, alice, link_json, "{}")
    with pytest.raises(UserError, match=message):
        chain.call(hub, alice, "apply", hex_of(fake))


def test_apply_rejects_a_link_with_a_bad_owner_field(chain, hub, alice):
    fake = chain.deploy(FakeSatellite, alice, fake_link(hub, alice, owner="oops"), "{}")
    with pytest.raises(UserError, match="address must be"):
        chain.call(hub, alice, "apply", hex_of(fake))


def test_apply_truncates_a_long_subject(chain, hub, alice):
    fake = chain.deploy(
        FakeSatellite, alice, fake_link(hub, alice, subject="s" * 500), "{}"
    )
    chain.call(hub, alice, "apply", hex_of(fake))
    assert len(get_record(chain, hub, fake, alice)["subject"]) == hallmark_hub.MAX_NAME


# ----------------------------------------------------------------------
# approve / revoke
# ----------------------------------------------------------------------


def test_approve_moves_pending_to_approved(chain, hub, dossier, alice, owner):
    register(chain, hub, dossier, alice)
    chain.call(hub, owner, "approve", hex_of(dossier))
    record = get_record(chain, hub, dossier, owner)
    assert record["status"] == "approved"
    assert record["effective_tier"] == "NONE"  # approved, but nothing attested yet
    assert record["is_current"] is False or record["tier"] == "NONE"


def test_approve_and_revoke_are_owner_only(chain, hub, dossier, alice, bob):
    register(chain, hub, dossier, alice)
    for method in ["approve", "revoke"]:
        with pytest.raises(UserError, match="only the hub owner"):
            chain.call(hub, alice, method, hex_of(dossier))
        with pytest.raises(UserError, match="only the hub owner"):
            chain.call(hub, bob, method, hex_of(dossier))


def test_approve_and_revoke_need_a_registered_dossier(chain, hub, owner, dossier):
    for method in ["approve", "revoke"]:
        with pytest.raises(UserError, match="not registered with this hub"):
            chain.call(hub, owner, method, hex_of(dossier))


def test_approve_twice_and_revoke_twice_fail(chain, hub, dossier, alice, owner):
    register(chain, hub, dossier, alice, approver=owner)
    with pytest.raises(UserError, match="already approved"):
        chain.call(hub, owner, "approve", hex_of(dossier))
    chain.call(hub, owner, "revoke", hex_of(dossier))
    with pytest.raises(UserError, match="already revoked"):
        chain.call(hub, owner, "revoke", hex_of(dossier))


def test_revoke_clears_the_result_but_keeps_counters(chain, hub, dossier, alice, owner, bob):
    audit_with(chain, dossier, alice, 92)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, bob, "attest", hex_of(dossier))
    assert get_record(chain, hub, dossier, bob)["effective_tier"] == "GOLD"

    chain.call(hub, owner, "revoke", hex_of(dossier))
    record = get_record(chain, hub, dossier, bob)
    assert record["status"] == "revoked"
    assert record["effective_tier"] == "NONE" and record["tier"] == "NONE"
    assert record["score"] == -1 and record["standard_version"] == 0
    assert record["is_current"] is False
    # kept on purpose, so the revoked audit cannot be attested again
    assert record["audit_count"] == 1 and record["attestations"] == 1


def test_a_pending_dossier_can_be_revoked_and_then_reinstated(
    chain, hub, dossier, alice, owner
):
    register(chain, hub, dossier, alice)
    chain.call(hub, owner, "revoke", hex_of(dossier))
    assert get_record(chain, hub, dossier, owner)["status"] == "revoked"
    chain.call(hub, owner, "approve", hex_of(dossier))
    record = get_record(chain, hub, dossier, owner)
    assert record["status"] == "approved" and record["effective_tier"] == "NONE"


# ----------------------------------------------------------------------
# attest with a real dossier
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "score, expected",
    [(100, "GOLD"), (90, "GOLD"), (89, "CERTIFIED"), (70, "CERTIFIED"), (69, "REJECTED"), (0, "REJECTED")],
)
def test_attest_assigns_the_tier_from_the_score(
    chain, hub, dossier, alice, owner, bob, score, expected
):
    audit_with(chain, dossier, alice, score)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, bob, "attest", hex_of(dossier))
    record = get_record(chain, hub, dossier, bob)
    assert record["tier"] == expected and record["effective_tier"] == expected
    assert record["score"] == score and record["is_current"] is True
    assert record["standard_version"] == 1 and record["audit_count"] == 1
    assert record["attestations"] == 1


def test_attest_is_open_to_anyone_and_accepts_lowercase_addresses(
    chain, hub, dossier, alice, owner, carol
):
    audit_with(chain, dossier, alice, 80)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, carol, "attest", lower(hex_of(dossier)))
    assert get_record(chain, hub, dossier, carol)["effective_tier"] == "CERTIFIED"


def test_attest_requires_approval(chain, hub, dossier, alice, owner, bob):
    audit_with(chain, dossier, alice, 92)
    with pytest.raises(UserError, match="not registered"):
        chain.call(hub, bob, "attest", hex_of(dossier))
    register(chain, hub, dossier, alice)
    with pytest.raises(UserError, match="must be approved"):
        chain.call(hub, bob, "attest", hex_of(dossier))
    chain.call(hub, owner, "approve", hex_of(dossier))
    chain.call(hub, owner, "revoke", hex_of(dossier))
    with pytest.raises(UserError, match="must be approved"):
        chain.call(hub, bob, "attest", hex_of(dossier))


def test_attest_needs_a_verdict(chain, hub, dossier, alice, owner, bob):
    register(chain, hub, dossier, alice, approver=owner)
    with pytest.raises(UserError, match="no verdict yet"):
        chain.call(hub, bob, "attest", hex_of(dossier))


def test_the_same_audit_cannot_be_attested_twice(chain, hub, dossier, alice, owner, bob):
    audit_with(chain, dossier, alice, 92)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, bob, "attest", hex_of(dossier))
    with pytest.raises(UserError, match="already been attested"):
        chain.call(hub, bob, "attest", hex_of(dossier))
    assert get_record(chain, hub, dossier, bob)["attestations"] == 1


def test_a_new_audit_can_be_attested_and_counts_up(chain, hub, dossier, alice, owner, bob):
    audit_with(chain, dossier, alice, 60)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, bob, "attest", hex_of(dossier))
    assert get_record(chain, hub, dossier, bob)["effective_tier"] == "REJECTED"
    audit_with(chain, dossier, alice, 95)
    chain.call(hub, bob, "attest", hex_of(dossier))
    record = get_record(chain, hub, dossier, bob)
    assert record["effective_tier"] == "GOLD" and record["score"] == 95
    assert record["audit_count"] == 2 and record["attestations"] == 2


def test_a_verdict_from_an_older_standard_is_refused(
    chain, hub, dossier, alice, owner, bob
):
    audit_with(chain, dossier, alice, 92)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, owner, "update_standard", "Standard v2", "Stricter", 80)
    with pytest.raises(UserError, match="older standard"):
        chain.call(hub, bob, "attest", hex_of(dossier))


def test_attested_results_turn_stale_when_the_standard_changes(
    chain, hub, dossier, alice, owner, bob
):
    audit_with(chain, dossier, alice, 92)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, bob, "attest", hex_of(dossier))
    chain.call(hub, owner, "update_standard", "Standard v2", "Stricter", 80)
    record = get_record(chain, hub, dossier, bob)
    assert record["effective_tier"] == "STALE" and record["is_current"] is False
    assert record["tier"] == "GOLD"  # the raw tier is kept for history
    # re-audit under v2 and attest again
    audit_with(chain, dossier, alice, 85)
    chain.call(hub, bob, "attest", hex_of(dossier))
    record = get_record(chain, hub, dossier, bob)
    assert record["effective_tier"] == "CERTIFIED" and record["standard_version"] == 2


def test_changing_pass_mark_changes_the_tier_of_the_next_attestation(
    chain, hub, dossier, alice, owner, bob
):
    audit_with(chain, dossier, alice, 92)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, owner, "update_standard", "Standard v2", "Very strict", 95)
    audit_with(chain, dossier, alice, 92)
    chain.call(hub, bob, "attest", hex_of(dossier))
    assert get_record(chain, hub, dossier, bob)["effective_tier"] == "REJECTED"


def test_reinstated_dossier_must_run_a_fresh_audit(
    chain, hub, dossier, alice, owner, bob
):
    audit_with(chain, dossier, alice, 92)
    register(chain, hub, dossier, alice, approver=owner)
    chain.call(hub, bob, "attest", hex_of(dossier))
    chain.call(hub, owner, "revoke", hex_of(dossier))
    chain.call(hub, owner, "approve", hex_of(dossier))

    record = get_record(chain, hub, dossier, bob)
    assert record["status"] == "approved" and record["effective_tier"] == "NONE"
    assert record["is_current"] is False
    with pytest.raises(UserError, match="cleared by a revocation"):
        chain.call(hub, bob, "attest", hex_of(dossier))

    audit_with(chain, dossier, alice, 88)
    chain.call(hub, bob, "attest", hex_of(dossier))
    record = get_record(chain, hub, dossier, bob)
    assert record["effective_tier"] == "CERTIFIED" and record["score"] == 88
    assert record["attestations"] == 2


# ----------------------------------------------------------------------
# attest against malformed or hostile satellites
# ----------------------------------------------------------------------


@pytest.fixture
def fake(chain, hub, alice, owner):
    """A registered, approved fake satellite whose verdict the test controls."""
    link = json.dumps({"hub": hex_of(hub), "owner": alice.as_hex, "subject": "Fake"})
    satellite = chain.deploy(FakeSatellite, alice, link, "{}")
    register(chain, hub, satellite, alice, approver=owner)
    return satellite


def good_verdict(**overrides):
    verdict = {"has_verdict": True, "score": 80, "standard_version": 1, "audit_count": 1}
    verdict.update(overrides)
    return json.dumps(verdict)


def test_attest_accepts_a_well_formed_fake_verdict(chain, hub, fake, alice, bob):
    chain.call(fake, alice, "set_verdict", good_verdict())
    chain.call(hub, bob, "attest", hex_of(fake))
    assert get_record(chain, hub, fake, bob)["effective_tier"] == "CERTIFIED"


@pytest.mark.parametrize(
    "verdict, message",
    [
        ("not json", "invalid data"),
        ("[]", "invalid data"),
        ('{"has_verdict": false}', "no verdict yet"),
        (good_verdict(has_verdict="true"), "no verdict yet"),
        (good_verdict(has_verdict=1), "no verdict yet"),
        (json.dumps({"has_verdict": True, "standard_version": 1, "audit_count": 1}), "malformed"),
        (json.dumps({"has_verdict": True, "score": 80, "audit_count": 1}), "malformed"),
        (json.dumps({"has_verdict": True, "score": 80, "standard_version": 1}), "malformed"),
        (good_verdict(score="abc"), "malformed"),
        (good_verdict(score=None), "malformed"),
        (good_verdict(score=101), "out of range"),
        (good_verdict(score=-1), "out of range"),
        (good_verdict(standard_version=2), "older standard"),
        (good_verdict(standard_version=0), "older standard"),
    ],
)
def test_attest_refuses_bad_verdicts_and_stores_nothing(
    chain, hub, fake, alice, bob, verdict, message
):
    chain.call(fake, alice, "set_verdict", verdict)
    before = get_record(chain, hub, fake, bob)
    with pytest.raises(UserError, match=message):
        chain.call(hub, bob, "attest", hex_of(fake))
    assert get_record(chain, hub, fake, bob) == before


def test_attest_reports_an_unreadable_dossier(chain, hub, fake, alice, bob, owner):
    # a registered address whose contract has no get_verdict()
    stranger = chain.deploy(hallmark_hub.HallmarkHub, owner, "R", "S", "C", 50)
    hub.records[hex_of(stranger)] = json.dumps(
        {
            "dossier": hex_of(stranger), "applicant": alice.as_hex, "subject": "x",
            "status": "approved", "score": -1, "tier": "NONE", "standard_version": 0,
            "audit_count": 0, "attestations": 0,
        }
    )
    with pytest.raises(UserError, match="could not read get_verdict"):
        chain.call(hub, bob, "attest", hex_of(stranger))


# ----------------------------------------------------------------------
# views
# ----------------------------------------------------------------------


def test_get_dossier_for_an_unknown_address(chain, hub, dossier, bob):
    with pytest.raises(UserError, match="not registered with this hub"):
        chain.call(hub, bob, "get_dossier", hex_of(dossier))
    with pytest.raises(UserError, match="address must be"):
        chain.call(hub, bob, "get_dossier", "0x12")


def make_registered(chain, hub, make_dossier, alice, count):
    dossiers = []
    for n in range(count):
        d = make_dossier(alice, subject=f"Subject {n}")
        chain.call(hub, alice, "apply", hex_of(d))
        dossiers.append(d)
    return dossiers


def test_list_dossiers_pages_in_registration_order(chain, hub, make_dossier, alice, bob):
    dossiers = make_registered(chain, hub, make_dossier, alice, 5)
    first = chain.view(hub, bob, "list_dossiers", 0, 2)
    assert first["total"] == 5
    assert [i["subject"] for i in first["items"]] == ["Subject 0", "Subject 1"]
    last = chain.view(hub, bob, "list_dossiers", 4, 10)
    assert [i["dossier"] for i in last["items"]] == [hex_of(dossiers[4])]
    everything = chain.view(hub, bob, "list_dossiers", 0, 100)
    assert [i["subject"] for i in everything["items"]] == [f"Subject {n}" for n in range(5)]


def test_list_dossiers_past_the_end_is_empty(chain, hub, make_dossier, alice, bob):
    make_registered(chain, hub, make_dossier, alice, 2)
    assert chain.view(hub, bob, "list_dossiers", 2, 5) == {"total": 2, "items": []}
    assert chain.view(hub, bob, "list_dossiers", 50, 5) == {"total": 2, "items": []}


def test_list_dossiers_on_an_empty_hub(chain, hub, bob):
    assert chain.view(hub, bob, "list_dossiers", 0, 10) == {"total": 0, "items": []}


def test_list_dossiers_caps_the_page_size(chain, hub, make_dossier, alice, bob):
    make_registered(chain, hub, make_dossier, alice, hallmark_hub.MAX_PAGE_SIZE + 2)
    page = chain.view(hub, bob, "list_dossiers", 0, 10_000)
    assert len(page["items"]) == hallmark_hub.MAX_PAGE_SIZE
    assert page["total"] == hallmark_hub.MAX_PAGE_SIZE + 2


@pytest.mark.parametrize("offset, limit", [(-1, 5), (0, 0), (0, -3)])
def test_list_dossiers_rejects_bad_arguments(chain, hub, bob, offset, limit):
    with pytest.raises(UserError, match="offset must be"):
        chain.call(hub, bob, "list_dossiers", offset, limit)


def test_list_dossiers_reports_effective_tiers(
    chain, hub, make_dossier, alice, owner, bob
):
    good = make_dossier(alice, subject="Good")
    pending = make_dossier(alice, subject="Pending")
    audit_with(chain, good, alice, 95)
    register(chain, hub, good, alice, approver=owner)
    chain.call(hub, bob, "attest", hex_of(good))
    register(chain, hub, pending, alice)

    tiers = {i["subject"]: i["effective_tier"] for i in chain.view(hub, bob, "list_dossiers", 0, 10)["items"]}
    assert tiers == {"Good": "GOLD", "Pending": "NONE"}

    chain.call(hub, owner, "update_standard", "v2", "c", 50)
    tiers = {i["subject"]: i["effective_tier"] for i in chain.view(hub, bob, "list_dossiers", 0, 10)["items"]}
    assert tiers == {"Good": "STALE", "Pending": "NONE"}


def test_summary_counts_registered_dossiers(chain, hub, make_dossier, alice, bob):
    make_registered(chain, hub, make_dossier, alice, 3)
    assert chain.view(hub, bob, "get_summary")["total_dossiers"] == 3

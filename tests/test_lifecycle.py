"""End-to-end stories that use only the public methods."""

from genlayer_stub import UserError
import pytest

from harness import audit_with, hex_of


def record(chain, hub, dossier, viewer):
    return chain.view(hub, viewer, "get_dossier", hex_of(dossier))


def test_full_certification_story(chain, hub, dossier, alice, owner, bob):
    # the subject prepares evidence and gets audited
    audit_with(chain, dossier, alice, 92, "Strong audit; thin complaint process.")
    # it joins the registry; the hub owner approves; anyone can attest
    chain.call(hub, alice, "apply", hex_of(dossier))
    assert record(chain, hub, dossier, bob)["effective_tier"] == "NONE"
    chain.call(hub, owner, "approve", hex_of(dossier))
    chain.call(hub, bob, "attest", hex_of(dossier))
    rec = record(chain, hub, dossier, bob)
    assert (rec["effective_tier"], rec["score"], rec["is_current"]) == ("GOLD", 92, True)

    # the owner tightens the standard: the old result is flagged, not trusted
    chain.call(hub, owner, "update_standard", "Standard v2", "Stricter rules", 95)
    assert record(chain, hub, dossier, bob)["effective_tier"] == "STALE"
    with pytest.raises(UserError, match="older standard"):
        chain.call(hub, bob, "attest", hex_of(dossier))

    # re-audit under v2: falls short of 95
    audit_with(chain, dossier, alice, 92)
    chain.call(hub, bob, "attest", hex_of(dossier))
    assert record(chain, hub, dossier, bob)["effective_tier"] == "REJECTED"

    # improve the evidence and try again
    audit_with(chain, dossier, alice, 97)
    chain.call(hub, bob, "attest", hex_of(dossier))
    rec = record(chain, hub, dossier, bob)
    assert (rec["effective_tier"], rec["score"]) == ("GOLD", 97)
    assert (rec["audit_count"], rec["attestations"], rec["standard_version"]) == (3, 3, 2)


def test_revocation_and_reinstatement_story(chain, hub, dossier, alice, owner, bob):
    audit_with(chain, dossier, alice, 85)
    chain.call(hub, alice, "apply", hex_of(dossier))
    chain.call(hub, owner, "approve", hex_of(dossier))
    chain.call(hub, bob, "attest", hex_of(dossier))
    assert record(chain, hub, dossier, bob)["effective_tier"] == "CERTIFIED"

    chain.call(hub, owner, "revoke", hex_of(dossier))
    assert record(chain, hub, dossier, bob)["effective_tier"] == "NONE"
    with pytest.raises(UserError, match="must be approved"):
        chain.call(hub, bob, "attest", hex_of(dossier))

    chain.call(hub, owner, "approve", hex_of(dossier))
    assert record(chain, hub, dossier, bob)["effective_tier"] == "NONE"
    with pytest.raises(UserError, match="cleared by a revocation"):
        chain.call(hub, bob, "attest", hex_of(dossier))

    audit_with(chain, dossier, alice, 91)
    chain.call(hub, bob, "attest", hex_of(dossier))
    assert record(chain, hub, dossier, bob)["effective_tier"] == "GOLD"


def test_ownership_transfer_hands_over_moderation(
    chain, hub, make_dossier, alice, owner, bob, carol
):
    other = make_dossier(carol, subject="Other")
    chain.call(hub, carol, "apply", hex_of(other))
    chain.call(hub, owner, "propose_owner", bob.as_hex)
    chain.call(hub, bob, "accept_ownership")

    with pytest.raises(UserError, match="only the hub owner"):
        chain.call(hub, owner, "approve", hex_of(other))
    chain.call(hub, bob, "approve", hex_of(other))
    assert record(chain, hub, other, carol)["status"] == "approved"


def test_two_subjects_are_tracked_independently(
    chain, hub, make_dossier, alice, owner, bob, carol
):
    first = make_dossier(alice, subject="First")
    second = make_dossier(carol, subject="Second")
    audit_with(chain, first, alice, 95)
    audit_with(chain, second, carol, 40)
    for dossier, applicant in [(first, alice), (second, carol)]:
        chain.call(hub, applicant, "apply", hex_of(dossier))
        chain.call(hub, owner, "approve", hex_of(dossier))
        chain.call(hub, bob, "attest", hex_of(dossier))
    chain.call(hub, owner, "revoke", hex_of(second))

    tiers = {
        item["subject"]: item["effective_tier"]
        for item in chain.view(hub, bob, "list_dossiers", 0, 10)["items"]
    }
    assert tiers == {"First": "GOLD", "Second": "NONE"}

# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *

import json

GOLD_MARK = 90
MAX_NAME = 80
MAX_TITLE = 120
MAX_CRITERIA = 3000
MAX_PAGE_SIZE = 50
ZERO_ADDRESS = "0x" + "0" * 40


def _require_text(label: str, value: str, max_len: int) -> str:
    value = value.strip()
    if len(value) == 0 or len(value) > max_len:
        raise gl.vm.UserError(f"{label} must be between 1 and {max_len} characters")
    return value


def _require_mark(pass_mark: int) -> int:
    if pass_mark < 1 or pass_mark > 100:
        raise gl.vm.UserError("pass_mark must be between 1 and 100")
    return pass_mark


def _normalize(address: str) -> str:
    address = address.strip()
    if len(address) != 42 or not address.startswith("0x"):
        raise gl.vm.UserError("address must be a 0x-prefixed 40-hex-character string")
    try:
        return Address(address).as_hex
    except Exception:
        raise gl.vm.UserError("invalid address")


def _tier(score: int, pass_mark: int) -> str:
    if score >= max(GOLD_MARK, pass_mark):
        return "GOLD"
    if score >= pass_mark:
        return "CERTIFIED"
    return "REJECTED"


def _with_status_fields(record: dict, current_version: int) -> dict:
    """Add is_current and effective_tier so consumers never trust a stale tier."""
    is_current = (
        record["status"] == "approved"
        and int(record["standard_version"]) == current_version
    )
    if record["status"] != "approved" or record["tier"] == "NONE":
        effective = "NONE"
    elif is_current:
        effective = record["tier"]
    else:
        effective = "STALE"
    record["is_current"] = is_current
    record["effective_tier"] = effective
    return record


def _read_json(raw: str, what: str) -> dict:
    try:
        data = json.loads(raw)
    except Exception:
        raise gl.vm.UserError(f"{what} returned invalid data")
    if not isinstance(data, dict):
        raise gl.vm.UserError(f"{what} returned invalid data")
    return data


class HallmarkHub(gl.Contract):
    """Hub of the Hallmark system.

    The hub owns one written quality standard. Satellite AssayDossier
    contracts (one per audited subject) are deployed with this hub's
    address, audit their own evidence against the standard with an LLM,
    and the hub pulls their verdicts into a public registry.
    """

    owner: Address
    pending_owner: str
    registry_name: str
    standard_title: str
    standard_criteria: str
    pass_mark: u256
    standard_version: u256
    records: TreeMap[str, str]
    index: DynArray[str]

    def __init__(
        self,
        registry_name: str,
        standard_title: str,
        standard_criteria: str,
        pass_mark: int,
    ):
        self.owner = gl.message.sender_address
        self.pending_owner = ""
        self.registry_name = _require_text("registry_name", registry_name, MAX_NAME)
        self.standard_title = _require_text("standard_title", standard_title, MAX_TITLE)
        self.standard_criteria = _require_text(
            "standard_criteria", standard_criteria, MAX_CRITERIA
        )
        self.pass_mark = u256(_require_mark(pass_mark))
        self.standard_version = u256(1)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _only_owner(self) -> None:
        if gl.message.sender_address.as_hex != self.owner.as_hex:
            raise gl.vm.UserError("only the hub owner can do this")

    def _load(self, key: str) -> dict:
        raw = self.records.get(key)
        if raw is None:
            raise gl.vm.UserError("dossier is not registered with this hub")
        return json.loads(raw)

    def _save(self, key: str, record: dict) -> None:
        self.records[key] = json.dumps(record)

    # ------------------------------------------------------------------
    # owner actions
    # ------------------------------------------------------------------

    @gl.public.write
    def update_standard(
        self, standard_title: str, standard_criteria: str, pass_mark: int
    ) -> None:
        self._only_owner()
        # Validate everything first and write afterwards, so a bad argument
        # can never leave the standard half updated.
        title = _require_text("standard_title", standard_title, MAX_TITLE)
        criteria = _require_text("standard_criteria", standard_criteria, MAX_CRITERIA)
        mark = _require_mark(pass_mark)
        self.standard_title = title
        self.standard_criteria = criteria
        self.pass_mark = u256(mark)
        self.standard_version = u256(int(self.standard_version) + 1)

    @gl.public.write
    def approve(self, dossier_address: str) -> None:
        self._only_owner()
        key = _normalize(dossier_address)
        record = self._load(key)
        if record["status"] == "approved":
            raise gl.vm.UserError("dossier is already approved")
        record["status"] = "approved"
        self._save(key, record)

    @gl.public.write
    def revoke(self, dossier_address: str) -> None:
        self._only_owner()
        key = _normalize(dossier_address)
        record = self._load(key)
        if record["status"] == "revoked":
            raise gl.vm.UserError("dossier is already revoked")
        record["status"] = "revoked"
        record["tier"] = "NONE"
        # Clear the old result too. Otherwise a reinstated dossier reports
        # is_current=true next to a stale score while it has no valid tier.
        # audit_count is kept on purpose: it stops the revoked audit from
        # being attested again.
        record["score"] = -1
        record["standard_version"] = 0
        self._save(key, record)

    @gl.public.write
    def propose_owner(self, new_owner: str) -> None:
        self._only_owner()
        target = _normalize(new_owner)
        if target == ZERO_ADDRESS:
            raise gl.vm.UserError("new owner cannot be the zero address")
        self.pending_owner = target

    @gl.public.write
    def accept_ownership(self) -> None:
        if self.pending_owner == "":
            raise gl.vm.UserError("no ownership transfer is pending")
        if gl.message.sender_address.as_hex != self.pending_owner:
            raise gl.vm.UserError("only the proposed owner can accept")
        self.owner = Address(self.pending_owner)
        self.pending_owner = ""

    @gl.public.write
    def cancel_ownership_proposal(self) -> None:
        self._only_owner()
        self.pending_owner = ""

    # ------------------------------------------------------------------
    # dossier lifecycle (pull model: the hub reads satellites, never the
    # other way round, so no cross-contract write messages are needed)
    # ------------------------------------------------------------------

    @gl.public.write
    def apply(self, dossier_address: str) -> None:
        key = _normalize(dossier_address)
        if key in self.records:
            raise gl.vm.UserError("dossier is already registered")

        try:
            raw = gl.get_contract_at(Address(key)).view().get_link()
        except Exception:
            raise gl.vm.UserError("could not read get_link() from the dossier")
        link = _read_json(raw, "dossier get_link()")

        if _normalize(str(link.get("hub", ""))) != gl.message.contract_address.as_hex:
            raise gl.vm.UserError("dossier is linked to a different hub")
        sender = gl.message.sender_address.as_hex
        if _normalize(str(link.get("owner", ""))) != sender:
            raise gl.vm.UserError("only the dossier owner can apply for it")

        record = {
            "dossier": key,
            "applicant": sender,
            "subject": str(link.get("subject", ""))[:MAX_NAME],
            "status": "pending",
            "score": -1,
            "tier": "NONE",
            "standard_version": 0,
            "audit_count": 0,
            "attestations": 0,
        }
        self._save(key, record)
        self.index.append(key)

    @gl.public.write
    def attest(self, dossier_address: str) -> None:
        key = _normalize(dossier_address)
        record = self._load(key)
        if record["status"] != "approved":
            raise gl.vm.UserError("dossier must be approved before it can be attested")

        try:
            raw = gl.get_contract_at(Address(key)).view().get_verdict()
        except Exception:
            raise gl.vm.UserError("could not read get_verdict() from the dossier")
        verdict = _read_json(raw, "dossier get_verdict()")

        if verdict.get("has_verdict") is not True:
            raise gl.vm.UserError("dossier has no verdict yet; run audit() first")
        try:
            score = int(verdict["score"])
            verdict_version = int(verdict["standard_version"])
            audit_count = int(verdict["audit_count"])
        except Exception:
            raise gl.vm.UserError("dossier verdict is malformed")
        if score < 0 or score > 100:
            raise gl.vm.UserError("dossier verdict is out of range")

        current_version = int(self.standard_version)
        if verdict_version != current_version:
            raise gl.vm.UserError(
                "verdict was issued under an older standard; run audit() again"
            )
        if audit_count <= int(record["audit_count"]):
            if int(record["score"]) < 0:
                # revoke() clears the score but keeps audit_count, so this is
                # an audit that was wiped by a revocation, not a repeat attest.
                raise gl.vm.UserError(
                    "this audit was cleared by a revocation; run audit() again"
                )
            raise gl.vm.UserError("this audit has already been attested")

        record["score"] = score
        record["tier"] = _tier(score, int(self.pass_mark))
        record["standard_version"] = verdict_version
        record["audit_count"] = audit_count
        record["attestations"] = int(record["attestations"]) + 1
        self._save(key, record)

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------

    @gl.public.view
    def get_standard(self) -> str:
        return json.dumps(
            {
                "hub": gl.message.contract_address.as_hex,
                "registry_name": self.registry_name,
                "title": self.standard_title,
                "criteria": self.standard_criteria,
                "pass_mark": int(self.pass_mark),
                "version": int(self.standard_version),
            }
        )

    @gl.public.view
    def get_summary(self) -> str:
        return json.dumps(
            {
                "registry_name": self.registry_name,
                "owner": self.owner.as_hex,
                "pending_owner": self.pending_owner,
                "standard_version": int(self.standard_version),
                "pass_mark": int(self.pass_mark),
                "total_dossiers": len(self.index),
            }
        )

    @gl.public.view
    def get_dossier(self, dossier_address: str) -> str:
        key = _normalize(dossier_address)
        record = self._load(key)
        return json.dumps(_with_status_fields(record, int(self.standard_version)))

    @gl.public.view
    def list_dossiers(self, offset: int, limit: int) -> str:
        if offset < 0 or limit < 1:
            raise gl.vm.UserError("offset must be >= 0 and limit must be >= 1")
        total = len(self.index)
        stop = min(offset + min(limit, MAX_PAGE_SIZE), total)
        current_version = int(self.standard_version)
        items = []
        for i in range(offset, stop):
            record = json.loads(self.records[self.index[i]])
            items.append(_with_status_fields(record, current_version))
        return json.dumps({"total": total, "items": items})

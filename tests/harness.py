"""Test helpers: a tiny chain simulator, fake contracts and shared constants."""

import copy
import json

import genlayer_stub as stub
from genlayer_stub import Address

gl = stub.gl

EVIDENCE_URL = "https://supplier.example.org/policy"
EVIDENCE_TEXT = (
    "Supplier list and sourcing policy. Third-party audit by BigAudit in 2025. "
    "Batch traceability portal. Complaint process."
)


def account(n: int) -> Address:
    return Address("0x" + format(n, "040x"))


def hex_of(contract) -> str:
    return contract._address.as_hex


def verdict_json(score, summary="ok") -> str:
    return json.dumps({"score": score, "summary": summary})


class FakeSatellite(gl.Contract):
    """A dossier-shaped contract whose answers the test controls.

    Used to check how the hub reacts to malformed or hostile satellites.
    """

    link_json: str
    verdict_json: str

    def __init__(self, link_json: str, verdict_json: str):
        self.link_json = link_json
        self.verdict_json = verdict_json

    @gl.public.view
    def get_link(self) -> str:
        return self.link_json

    @gl.public.view
    def get_verdict(self) -> str:
        return self.verdict_json

    @gl.public.write
    def set_verdict(self, verdict_json: str) -> None:
        self.verdict_json = verdict_json


class FakeHub(gl.Contract):
    """A hub-shaped contract whose get_standard() answer the test controls."""

    standard_json: str

    def __init__(self, standard_json: str):
        self.standard_json = standard_json

    @gl.public.view
    def get_standard(self) -> str:
        return self.standard_json


def _snapshot(contract):
    return copy.deepcopy(
        {k: v for k, v in vars(contract).items() if not k.startswith("_")}
    )


class Chain:
    """Deploys contracts and calls them as a chosen sender."""

    def __init__(self):
        stub.reset()
        self._next = 1000

    # -- setup -----------------------------------------------------------

    def deploy(self, cls, sender, *args):
        stub.message.sender_address = sender
        self._next += 1
        address = account(self._next)
        stub.message.contract_address = address
        contract = cls(*args)
        contract._address = address
        stub.REGISTRY[address.as_hex] = contract
        return contract

    def web(self, url, page):
        """Set what the validators' browser returns (text or an Exception)."""
        stub.STATE["webpages"][url] = page

    def llm(self, *responses):
        """Queue LLM replies: one per call, the last one repeats.

        A reply may be a string or a function taking the prompt.
        """
        stub.STATE["llm"] = list(responses)
        stub.STATE["llm_calls"] = 0

    @property
    def prompts(self):
        return stub.STATE["prompts"]

    # -- calls -----------------------------------------------------------

    def call(self, contract, sender, method, *args):
        stub.message.sender_address = sender
        stub.message.contract_address = contract._address
        function = getattr(contract, method)
        is_view = getattr(function, "_kind", None) == "view"
        before = _snapshot(contract) if is_view else None
        result = function(*args)
        if is_view:
            assert _snapshot(contract) == before, f"view {method}() changed storage"
        return result

    def view(self, contract, sender, method, *args):
        """Call a view method and parse its JSON answer."""
        return json.loads(self.call(contract, sender, method, *args))


def audit_with(chain, dossier, sender, score, summary="ok"):
    """Make sure the dossier has evidence, then run an audit that scores `score`."""
    if chain.view(dossier, sender, "get_info")["evidence_count"] == 0:
        chain.call(dossier, sender, "add_evidence", EVIDENCE_URL, "policy page")
    chain.llm(verdict_json(score, summary))
    chain.call(dossier, sender, "audit")

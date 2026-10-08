import importlib.util
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The contracts do `from genlayer import *`; give them the in-memory stub.
import genlayer_stub  # noqa: E402

sys.modules["genlayer"] = genlayer_stub


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hallmark_hub = _load("hallmark_hub", "hallmark_hub.py")
assay_dossier = _load("assay_dossier", "assay_dossier.py")

from harness import (  # noqa: E402
    EVIDENCE_TEXT,
    EVIDENCE_URL,
    Chain,
    account,
    verdict_json,
)

HUB_ARGS = ("Test Registry", "Quality Standard v1", "Be good. Show evidence.", 70)


@pytest.fixture
def chain():
    chain = Chain()
    chain.web(EVIDENCE_URL, EVIDENCE_TEXT)
    chain.llm(verdict_json(92, "Strong audit; thin complaint process."))
    return chain


@pytest.fixture
def owner():
    return account(1)


@pytest.fixture
def alice():
    return account(2)


@pytest.fixture
def bob():
    return account(3)


@pytest.fixture
def carol():
    return account(4)


@pytest.fixture
def hub(chain, owner):
    return chain.deploy(hallmark_hub.HallmarkHub, owner, *HUB_ARGS)


@pytest.fixture
def make_dossier(chain, hub):
    def factory(sender, subject="Acme Coffee", description="Acme coffee supplier", on=None):
        target = on if on is not None else hub
        return chain.deploy(
            assay_dossier.AssayDossier,
            sender,
            target._address.as_hex,
            subject,
            description,
        )

    return factory


@pytest.fixture
def dossier(make_dossier, alice):
    return make_dossier(alice)

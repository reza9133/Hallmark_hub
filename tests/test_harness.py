"""Checks on the fake SDK itself, so the other tests can be trusted."""

import pytest

import genlayer_stub as stub
from genlayer_stub import Address, u256
from harness import FakeSatellite, account, hex_of

gl = stub.gl


def test_keccak256_known_vectors():
    assert stub.keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert stub.keccak256(b"abc").hex() == (
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )


@pytest.mark.parametrize(
    "checksummed",
    [
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ],
)
def test_address_as_hex_matches_eip55_vectors(checksummed):
    assert Address(checksummed.lower()).as_hex == checksummed
    assert Address(checksummed).as_hex == checksummed


def test_address_rejects_bad_input():
    for bad in ["0x123", "", "5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "0x" + "zz" * 20]:
        with pytest.raises(ValueError):
            Address(bad)


def test_u256_rejects_out_of_range():
    assert u256(5) == 5
    with pytest.raises(OverflowError):
        u256(-1)
    with pytest.raises(OverflowError):
        u256(1 << 256)


def test_storage_fields_default_to_empty_values(chain, owner):
    contract = chain.deploy(FakeSatellite, owner, "{}", "{}")
    assert contract.link_json == "{}"


def test_view_that_writes_storage_is_caught(chain, owner):
    class Sneaky(gl.Contract):
        counter: u256

        def __init__(self):
            pass

        @gl.public.view
        def peek(self) -> str:
            self.counter = u256(int(self.counter) + 1)
            return "{}"

    contract = chain.deploy(Sneaky, owner)
    with pytest.raises(AssertionError, match="changed storage"):
        chain.call(contract, owner, "peek")


def test_view_proxy_only_exposes_views(chain, owner):
    satellite = chain.deploy(FakeSatellite, owner, "{}", "{}")
    stub.message.contract_address = account(5)
    proxy = stub.gl.get_contract_at(satellite._address).view()
    assert proxy.get_link() == "{}"
    with pytest.raises(AttributeError):
        proxy.set_verdict("{}")


def test_get_contract_at_missing_contract_raises(chain):
    with pytest.raises(Exception, match="no contract"):
        stub.gl.get_contract_at(account(424242))


def test_cross_contract_call_switches_message_context(chain, owner, hub):
    stub.message.sender_address = owner
    stub.message.contract_address = account(5)
    stub.gl.get_contract_at(hub._address).view().get_standard()
    assert stub.message.contract_address == account(5)
    assert stub.message.sender_address == owner


def test_nondet_result_must_be_calldata_encodable():
    with pytest.raises(TypeError):
        gl.vm.run_nondet_unsafe(lambda: {"x": object()}, lambda leader: True)


def test_validator_rejection_raises_consensus_error():
    with pytest.raises(stub.ConsensusError):
        gl.vm.run_nondet_unsafe(lambda: {"x": 1}, lambda leader: False)


def test_hex_of_returns_checksummed_address(chain, owner):
    satellite = chain.deploy(FakeSatellite, owner, "{}", "{}")
    assert hex_of(satellite) == satellite._address.as_hex

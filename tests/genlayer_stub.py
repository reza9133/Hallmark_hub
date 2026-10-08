"""A small in-memory stand-in for the GenLayer contract SDK.

It lets the Hallmark contracts run as ordinary Python, so their logic can be
tested without a GenVM. conftest.py registers this module as `genlayer` before
the contracts are imported.

What it models
  * Address with the real EIP-55 checksum form of `as_hex`.
  * u256 range checking, TreeMap / DynArray storage containers, and storage
    fields that default to empty values, as in a real contract.
  * gl.message, which changes during a cross-contract call.
  * gl.get_contract_at(...).view(): only methods marked @gl.public.view can be
    reached, and a missing contract raises, as on the network.
  * gl.vm.run_nondet_unsafe: runs the leader function, then the validator
    function on the leader's result. A validator that returns False raises
    ConsensusError. Results must be calldata-encodable.
  * gl.nondet.web.render and gl.nondet.exec_prompt, driven by test data.

What it does NOT model
  * Real consensus, real LLMs, real web access or gas.
  * Rollback of storage after a failed call. This is deliberate: a contract
    must validate before it writes, and the tests are stricter without a
    safety net.
"""

import copy
import types

__all__ = ["gl", "Address", "u256", "TreeMap", "DynArray"]


class UserError(Exception):
    """gl.vm.UserError: a failure the contract raises on purpose."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ConsensusError(Exception):
    """Raised when the validator function rejects the leader's result."""


class Return:
    """gl.vm.Return: a successful leader result, as the validator sees it."""

    def __init__(self, calldata):
        self.calldata = calldata


# ----------------------------------------------------------------------
# Keccak-256 (needed only for the EIP-55 address checksum)
# ----------------------------------------------------------------------

_MASK = (1 << 64) - 1
_ROUND_CONSTANTS = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROTATIONS = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def _rotl(value, shift):
    shift %= 64
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _permute(state):
    for constant in _ROUND_CONSTANTS:
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4]
             for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        state = [[state[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(state[x][y], _ROTATIONS[x][y])
        state = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
                  for y in range(5)] for x in range(5)]
        state[0][0] ^= constant
    return state


def keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0)
    padded[-1] |= 0x80
    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[8 * i:8 * i + 8], "little")
            state[i % 5][i // 5] ^= lane
        state = _permute(state)
    return b"".join(state[i % 5][i // 5].to_bytes(8, "little") for i in range(4))


# ----------------------------------------------------------------------
# SDK types
# ----------------------------------------------------------------------


class Address:
    __slots__ = ("_bytes",)

    def __init__(self, val):
        if isinstance(val, str):
            if len(val) != 42 or not val.startswith("0x"):
                raise ValueError("address string must be 0x plus 40 hex characters")
            raw = bytes.fromhex(val[2:])
        else:
            raw = bytes(val)
        if len(raw) != 20:
            raise ValueError("address must be 20 bytes")
        self._bytes = raw

    @property
    def as_bytes(self):
        return self._bytes

    @property
    def as_hex(self):
        plain = self._bytes.hex()
        digest = keccak256(plain.encode("ascii")).hex()
        out = "".join(
            ch.upper() if ch.isalpha() and int(digest[i], 16) >= 8 else ch
            for i, ch in enumerate(plain)
        )
        return "0x" + out

    def __eq__(self, other):
        return isinstance(other, Address) and other._bytes == self._bytes

    def __hash__(self):
        return hash(self._bytes)

    def __repr__(self):
        return f"Address({self.as_hex})"


class u256(int):
    def __new__(cls, value=0):
        value = int(value)
        if value < 0 or value >= 1 << 256:
            raise OverflowError("value does not fit in u256")
        return super().__new__(cls, value)


class TreeMap(dict):
    def __class_getitem__(cls, item):
        return cls


class DynArray(list):
    def __class_getitem__(cls, item):
        return cls


# ----------------------------------------------------------------------
# Contract base class, registry and message context
# ----------------------------------------------------------------------

message = types.SimpleNamespace(sender_address=None, contract_address=None)
REGISTRY = {}
STATE = {"webpages": {}, "llm": [], "llm_calls": 0, "prompts": []}


def reset():
    REGISTRY.clear()
    STATE["webpages"] = {}
    STATE["llm"] = []
    STATE["llm_calls"] = 0
    STATE["prompts"] = []
    message.sender_address = None
    message.contract_address = None


class _ContractMeta(type):
    """Give every annotated storage field a default, then run __init__."""

    def __call__(cls, *args, **kwargs):
        obj = cls.__new__(cls)
        for name, annotation in getattr(cls, "__annotations__", {}).items():
            if annotation is TreeMap:
                value = TreeMap()
            elif annotation is DynArray:
                value = DynArray()
            elif annotation is str:
                value = ""
            elif annotation is bool:
                value = False
            elif annotation is u256:
                value = u256(0)
            else:
                value = None
            setattr(obj, name, value)
        obj.__init__(*args, **kwargs)
        return obj


class Contract(metaclass=_ContractMeta):
    pass


def _write(fn):
    fn._kind = "write"
    return fn


def _view(fn):
    fn._kind = "view"
    return fn


class _ViewProxy:
    def __init__(self, target, caller):
        self._target = target
        self._caller = caller

    def view(self):
        return self

    def __getattr__(self, name):
        method = getattr(type(self._target), name, None)
        if getattr(method, "_kind", None) != "view":
            raise AttributeError(f"{name} is not a public view method")
        bound = getattr(self._target, name)
        target = self._target
        caller = self._caller

        def call(*args, **kwargs):
            saved = (message.sender_address, message.contract_address)
            message.sender_address = caller
            message.contract_address = target._address
            try:
                return bound(*args, **kwargs)
            finally:
                message.sender_address, message.contract_address = saved

        return call


def _get_contract_at(address):
    if not isinstance(address, Address):
        raise TypeError("get_contract_at expects an Address")
    target = REGISTRY.get(address.as_hex)
    if target is None:
        raise Exception(f"no contract is deployed at {address.as_hex}")
    return _ViewProxy(target, message.contract_address)


def _check_calldata(value):
    if value is None or isinstance(value, (bool, int, str, bytes, Address)):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _check_calldata(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("calldata dict keys must be strings")
            _check_calldata(item)
        return
    raise TypeError(f"{type(value).__name__} cannot be encoded as calldata")


def _run_nondet_unsafe(leader_fn, validator_fn):
    result = leader_fn()
    _check_calldata(result)
    if not validator_fn(Return(copy.deepcopy(result))):
        raise ConsensusError("the validator did not accept the leader's result")
    return result


def _render(url, mode="text"):
    page = STATE["webpages"].get(url)
    if page is None:
        raise Exception(f"could not load {url}")
    if isinstance(page, Exception):
        raise page
    return page


def _exec_prompt(prompt):
    STATE["prompts"].append(prompt)
    responses = STATE["llm"]
    if not responses:
        raise Exception("no LLM response was configured for this test")
    index = min(STATE["llm_calls"], len(responses) - 1)
    STATE["llm_calls"] += 1
    response = responses[index]
    return response(prompt) if callable(response) else response


gl = types.SimpleNamespace(
    Contract=Contract,
    public=types.SimpleNamespace(write=_write, view=_view),
    message=message,
    vm=types.SimpleNamespace(
        UserError=UserError,
        Return=Return,
        run_nondet_unsafe=_run_nondet_unsafe,
    ),
    nondet=types.SimpleNamespace(
        web=types.SimpleNamespace(render=_render),
        exec_prompt=_exec_prompt,
    ),
    get_contract_at=_get_contract_at,
)

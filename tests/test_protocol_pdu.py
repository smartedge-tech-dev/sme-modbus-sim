import struct

import pytest

from sme_modbus_sim.protocol.errors import ModbusError
from sme_modbus_sim.protocol.pdu import describe_request, describe_response, handle_pdu
from sme_modbus_sim.protocol.store import UnitStore


def req(fc, a, b, extra=b""):
    return bytes([fc]) + struct.pack(">HH", a, b) + extra


@pytest.fixture
def store():
    s = UnitStore(1)
    s.define("coil", 0, 20)
    s.define("discrete", 0, 20)
    s.define("holding", 0, 200)
    s.define("holding", 300, 2, writable=False)
    s.define("input", 0, 200)
    return s


def test_fc01_fc02(store):
    store.set("coil", 0, [1, 0, 1, 1, 0, 0, 0, 0, 1])
    assert handle_pdu(store, req(0x01, 0, 9)) == bytes([0x01, 2, 0b00001101, 0x01])
    store.set("discrete", 3, [1])
    assert handle_pdu(store, req(0x02, 0, 4)) == bytes([0x02, 1, 0b1000])


def test_fc03_fc04(store):
    store.set("holding", 10, [0x1234, 0xABCD])
    assert handle_pdu(store, req(0x03, 10, 2)) == bytes([0x03, 4, 0x12, 0x34, 0xAB, 0xCD])
    store.set("input", 0, [7])
    assert handle_pdu(store, req(0x04, 0, 1)) == bytes([0x04, 2, 0, 7])
    assert len(handle_pdu(store, req(0x03, 0, 125))) == 2 + 250


def test_fc05_fc06(store):
    p = req(0x05, 3, 0xFF00)
    assert handle_pdu(store, p) == p
    assert store.get("coil", 3) == [1]
    assert handle_pdu(store, req(0x05, 3, 0x0000)) == req(0x05, 3, 0)
    assert store.get("coil", 3) == [0]
    p = req(0x06, 5, 0xBEEF)
    assert handle_pdu(store, p) == p
    assert store.get("holding", 5) == [0xBEEF]


def test_fc0f_fc10(store):
    p = req(0x0F, 2, 10, bytes([2, 0b11001101, 0b01]))
    assert handle_pdu(store, p) == req(0x0F, 2, 10)
    assert store.get("coil", 2, 10) == [1, 0, 1, 1, 0, 0, 1, 1, 1, 0]
    p = req(0x10, 20, 2, bytes([4, 0, 1, 0, 2]))
    assert handle_pdu(store, p) == req(0x10, 20, 2)
    assert store.get("holding", 20, 2) == [1, 2]


def exc(fc, code):
    return bytes([fc | 0x80, code])


def test_unknown_fc(store):
    assert handle_pdu(store, bytes([0x07])) == exc(0x07, 1)
    assert handle_pdu(store, bytes([0x2B, 0x0E, 1, 0])) == exc(0x2B, 1)


@pytest.mark.parametrize(
    "pdu",
    [
        req(0x01, 0, 0), req(0x01, 0, 2001), req(0x02, 0, 0), req(0x02, 0, 2001),
        req(0x03, 0, 0), req(0x03, 0, 126), req(0x04, 0, 0), req(0x04, 0, 126),
        req(0x05, 0, 0x1234),
        req(0x0F, 0, 0, bytes([0])), req(0x0F, 0, 1969, bytes([247]) + bytes(247)),
        req(0x0F, 0, 9, bytes([1, 0])),           # byte count mismatch
        req(0x0F, 0, 9, bytes([2, 0])),           # data shorter than byte count
        req(0x10, 0, 0, bytes([0])), req(0x10, 0, 124, bytes([248]) + bytes(248)),
        req(0x10, 0, 2, bytes([3, 0, 0, 0])),     # byte count != 2*qty
        bytes([0x03, 0, 0]),                      # truncated PDU
        req(0x03, 0, 1) + b"\x00",                # trailing bytes
    ],
)
def test_illegal_data_value(store, pdu):
    assert handle_pdu(store, pdu) == exc(pdu[0], 3)


@pytest.mark.parametrize(
    "pdu",
    [
        req(0x01, 15, 10), req(0x02, 100, 1), req(0x03, 199, 2), req(0x04, 500, 1),
        req(0x05, 50, 0xFF00), req(0x06, 250, 1), req(0x06, 300, 1),  # read-only
        req(0x0F, 19, 2, bytes([1, 3])), req(0x10, 199, 2, bytes([4, 0, 0, 0, 0])),
        req(0x03, 0xFFFF, 2),  # beyond address space
    ],
)
def test_illegal_data_address(store, pdu):
    assert handle_pdu(store, pdu) == exc(pdu[0], 2)


def test_check_order_value_before_address(store):
    # bad quantity AND undefined address -> 03 wins
    assert handle_pdu(store, req(0x03, 5000, 200)) == exc(0x03, 3)
    assert handle_pdu(store, req(0x05, 5000, 0x1234)) == exc(0x05, 3)


def test_undefined_zero_policy():
    s = UnitStore(1, undefined_address="zero")
    s.define("holding", 0, 1)
    assert handle_pdu(s, req(0x03, 0, 3)) == bytes([3, 6, 0, 0, 0, 0, 0, 0])
    assert handle_pdu(s, req(0x01, 100, 1)) == bytes([1, 1, 0])
    # writes to undefined addresses are still rejected
    assert handle_pdu(s, req(0x06, 5, 1)) == exc(0x06, 2)


def test_on_write_and_validator(store):
    calls = []
    store.on_write(lambda t, a, c: calls.append((t, a, c)))
    handle_pdu(store, req(0x10, 0, 2, bytes([4, 0, 1, 0, 2])))
    handle_pdu(store, req(0x05, 1, 0xFF00))
    assert calls == [("holding", 0, 2), ("coil", 1, 1)]

    def reject_partial(table, address, values):
        if table == "holding" and address == 50 and len(values) < 2:
            raise ModbusError(2, "partial write")

    store.add_write_validator(reject_partial)
    assert handle_pdu(store, req(0x06, 50, 1)) == exc(0x06, 2)
    assert len(calls) == 2  # rejected write does not fire callback


def test_execution_failure_maps_to_04(store):
    store.on_write(lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert handle_pdu(store, req(0x06, 0, 1)) == exc(0x06, 4)


def test_store_raw_access_and_define_errors():
    s = UnitStore(3)
    s.set("input", 10, [5])  # engine bypasses definitions
    assert s.get("input", 10) == [5]
    with pytest.raises(ValueError):
        s.define("input", 0, 1, writable=True)
    with pytest.raises(ValueError):
        s.define("holding", 65535, 2)
    with pytest.raises(ValueError):
        UnitStore(1, undefined_address="bogus")
    s.define("input", 0, 1)
    with pytest.raises(ModbusError) as ei:
        s.write("input", 0, [1])
    assert ei.value.exception_code == 2


def test_describe():
    assert describe_request(req(0x03, 10, 2)) == "FC03 read holding addr=10 qty=2"
    assert "value=0xFF00" in describe_request(req(0x05, 1, 0xFF00))
    assert describe_response(exc(0x03, 2)) == ("FC03 exception 02", 2)
    assert describe_response(bytes([3, 2, 0, 1]))[1] is None

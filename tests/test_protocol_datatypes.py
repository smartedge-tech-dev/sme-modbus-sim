import math

import pytest

from sme_modbus_sim.protocol.datatypes import decode, encode, get_bit, register_count, set_bit

ORDERS = [("big", "big"), ("big", "little"), ("little", "big"), ("little", "little")]

SAMPLES = {
    "uint16": [0, 1, 0xFFFF, 12345],
    "int16": [-32768, -1, 0, 32767],
    "uint32": [0, 0xFFFFFFFF, 123456789],
    "int32": [-0x80000000, -2, 0x7FFFFFFF],
    "float32": [0.0, -1.5, 3.25, 1e10],
    "uint64": [0, 0xFFFFFFFFFFFFFFFF, 2**40 + 7],
    "int64": [-(2**63), -1, 2**63 - 1],
    "float64": [0.0, math.pi, -1e300],
}


@pytest.mark.parametrize("byte_order,word_order", ORDERS)
@pytest.mark.parametrize("datatype", list(SAMPLES))
def test_round_trip_all_orders(datatype, byte_order, word_order):
    for value in SAMPLES[datatype]:
        words = encode(value, datatype, byte_order, word_order)
        assert len(words) == register_count(datatype)
        assert all(0 <= w <= 0xFFFF for w in words)
        out = decode(words, datatype, byte_order, word_order)
        if datatype == "float32":
            assert out == pytest.approx(value, rel=1e-6)
        else:
            assert out == value


@pytest.mark.parametrize("byte_order,word_order", ORDERS)
def test_string_and_bool_round_trip(byte_order, word_order):
    words = encode("ABC", "string", byte_order, word_order, count=3)
    assert len(words) == 3
    assert decode(words, "string", byte_order, word_order, count=3) == "ABC"
    assert encode(True, "bool") == [1]
    assert decode([0], "bool") is False


def test_order_layouts_abcd():
    # 0x11223344 -> ABCD / CDAB / BADC / DCBA
    assert encode(0x11223344, "uint32", "big", "big") == [0x1122, 0x3344]
    assert encode(0x11223344, "uint32", "big", "little") == [0x3344, 0x1122]
    assert encode(0x11223344, "uint32", "little", "big") == [0x2211, 0x4433]
    assert encode(0x11223344, "uint32", "little", "little") == [0x4433, 0x2211]
    assert encode(1.0, "float32") == [0x3F80, 0x0000]


def test_string_layout_and_padding():
    assert encode("AB", "string", count=2) == [0x4142, 0x0000]
    assert encode("AB", "string", "little", count=1) == [0x4241]
    with pytest.raises(ValueError):
        encode("ABCDE", "string", count=2)


def test_scale_offset():
    words = encode(230.5, "uint16", scale=0.1)
    assert words == [2305]
    assert decode(words, "uint16", scale=0.1) == pytest.approx(230.5)
    words = encode(-10.0, "int16", scale=0.5, offset=10.0)
    assert words == [(-40) & 0xFFFF]
    assert decode(words, "int16", scale=0.5, offset=10.0) == pytest.approx(-10.0)
    # float types store the unrounded quotient
    words = encode(1.25, "float32", scale=0.5)
    assert decode(words, "float32") == pytest.approx(2.5)


def test_out_of_range_raises():
    with pytest.raises(ValueError):
        encode(70000, "uint16")
    with pytest.raises(ValueError):
        encode(-1, "uint32")
    with pytest.raises(ValueError):
        encode(1e40, "float32")
    with pytest.raises(ValueError):
        encode(1, "nope")
    with pytest.raises(ValueError):
        encode(1, "uint16", byte_order="middle")


def test_register_count():
    assert register_count("bool") == 1
    assert register_count("bit") == 1
    assert register_count("int16") == 1
    assert register_count("float32") == 2
    assert register_count("int64") == 4
    assert register_count("string", 8) == 8


def test_bits():
    assert get_bit(0b1000, 3) is True
    assert get_bit(0b1000, 2) is False
    assert set_bit(0, 15, True) == 0x8000
    assert set_bit(0xFFFF, 0, False) == 0xFFFE
    with pytest.raises(ValueError):
        get_bit(0, 16)

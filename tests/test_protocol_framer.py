import pytest
from sme_modbus_sim.protocol.framer import (
    DroppedFrame,
    Frame,
    MbapFramer,
    RtuFramer,
    crc16,
    crc_bytes,
)


def test_crc_known_vectors():
    assert crc_bytes(bytes.fromhex("01030000000A")) == bytes.fromhex("C5CD")
    assert crc_bytes(bytes.fromhex("010300000001")) == bytes.fromhex("840A")
    assert crc16(b"") == 0xFFFF


def test_mbap_build_and_parse():
    raw = MbapFramer.build(0x1234, 1, bytes.fromhex("0300000002"))
    assert raw == bytes.fromhex("1234000000060103 00000002".replace(" ", ""))
    items = MbapFramer().feed(raw)
    assert items == [Frame(1, bytes.fromhex("0300000002"), raw, 0x1234)]


def test_mbap_partial_reassembly_and_pipelining():
    f = MbapFramer()
    a = MbapFramer.build(1, 1, bytes.fromhex("0300000002"))
    b = MbapFramer.build(2, 2, bytes.fromhex("0400100001"))
    stream = a + b
    out = []
    for i in range(len(stream)):
        out += f.feed(stream[i:i + 1])
    assert [(x.transaction_id, x.unit_id) for x in out] == [(1, 1), (2, 2)]
    assert not f.has_partial
    # both frames in one chunk
    out = f.feed(a + b)
    assert len(out) == 2


def test_mbap_protocol_id_and_length():
    f = MbapFramer()
    bad_pid = bytes.fromhex("0001000100060103 00000001".replace(" ", ""))
    good = MbapFramer.build(5, 1, bytes.fromhex("0300000001"))
    out = f.feed(bad_pid + good)
    assert isinstance(out[0], DroppedFrame) and not out[0].fatal
    assert isinstance(out[1], Frame) and out[1].transaction_id == 5
    out = MbapFramer().feed(bytes.fromhex("000100000001 01".replace(" ", "")))
    assert isinstance(out[0], DroppedFrame) and out[0].fatal  # length 1 < 2
    out = MbapFramer().feed(bytes.fromhex("0001000000FF01"))
    assert out[0].fatal  # length 255 > 254


def test_rtu_fixed_and_variable_frames():
    r1 = RtuFramer.build(1, bytes.fromhex("030000000A"))
    assert r1 == bytes.fromhex("01030000000AC5CD")
    r2 = RtuFramer.build(2, bytes.fromhex("1000000002 04 00010002".replace(" ", "")))
    f = RtuFramer()
    out = []
    for i in range(len(r1 + r2)):
        out += f.feed((r1 + r2)[i:i + 1])
    assert [(x.unit_id, x.pdu[0]) for x in out] == [(1, 3), (2, 0x10)]
    assert not f.has_partial


def test_rtu_bad_crc_dropped():
    bad = bytes.fromhex("01030000000AC5CE")
    out = RtuFramer().feed(bad)
    assert len(out) == 1 and isinstance(out[0], DroppedFrame)
    assert out[0].reason == "CRC mismatch"


def test_rtu_unknown_fc_flush_partial():
    frame = RtuFramer.build(1, bytes.fromhex("2B0D0100"))
    f = RtuFramer()
    assert f.feed(frame) == []
    assert f.has_partial
    out = f.flush_partial()
    assert isinstance(out[0], Frame) and out[0].pdu == bytes.fromhex("2B0D0100")
    assert f.flush_partial() == []
    f.feed(b"\x01")
    assert isinstance(f.flush_partial()[0], DroppedFrame)


@pytest.mark.parametrize("pdu", [
    bytes.fromhex("1600040F0F0001"),                 # FC16
    bytes.fromhex("17000000020001000104AAAABBBB"),   # FC17
    bytes.fromhex("2B0E0100"),                       # FC2B / 0E
])
def test_rtu_length_new_fcs(pdu):
    frame = RtuFramer.build(1, pdu)
    out = RtuFramer().feed(frame + frame)
    assert [o.pdu for o in out] == [pdu, pdu]

"""Modbus ASCII: framer, listener config, and a serial ASCII listener over pyserial's socket://."""
import asyncio

import pytest

from sme_modbus_sim.config import SimConfig
from sme_modbus_sim.protocol.framer import AsciiFramer, DroppedFrame, Frame, lrc
from sme_modbus_sim.protocol.server import encode_response

serial = pytest.importorskip("serial")

from test_serial import Master, _units  # noqa: E402

from sme_modbus_sim.protocol.serial_server import ModbusSerialServer  # noqa: E402

READ_1 = bytes.fromhex("0300000001")


# ------------------------------------------------------------------ framer
def test_lrc_and_build():
    # address 0x11, FC03, start 0x006B, qty 3: sum 0x82 -> LRC 0x7E
    assert lrc(bytes.fromhex("1103006B0003")) == 0x7E
    assert AsciiFramer.build(0x11, bytes.fromhex("03006B0003")) == b":1103006B00037E\r\n"
    assert AsciiFramer.build(1, b"\x03", delimiter=0x21).endswith(b"\r!")


def test_feed_split_lowercase_and_noise():
    f = AsciiFramer()
    raw = AsciiFramer.build(1, READ_1)
    assert f.feed(b"\x00junk" + raw[:4]) == [] and f.has_partial
    out = f.feed(raw[4:] + raw.lower())
    assert [type(i) for i in out] == [Frame, Frame]
    assert (out[0].unit_id, out[0].pdu, out[1].pdu) == (1, READ_1, READ_1)
    assert not f.has_partial


@pytest.mark.parametrize("data, reason", [
    (b":010300000001FA\r\n", "LRC mismatch"),
    (b":0103000000010\r\n", "odd number"),
    (b":01030000000GFB\r\n", "invalid character"),
    (b":01FE\r\n", "too short"),
])
def test_bad_frames_dropped(data, reason):
    (item,) = AsciiFramer().feed(data)
    assert isinstance(item, DroppedFrame) and reason in item.reason


def test_new_start_discards_partial_and_timeout_flush():
    f = AsciiFramer()
    raw = AsciiFramer.build(1, READ_1)
    out = f.feed(b":0103" + raw)
    assert isinstance(out[0], DroppedFrame) and "new ':'" in out[0].reason and isinstance(out[1], Frame)
    f.feed(b":0103")
    (item,) = f.flush_partial()
    assert isinstance(item, DroppedFrame) and item.reason == "inter-character timeout" and not f.has_partial
    assert f.flush_partial() == []


def test_overlong_frame_dropped():
    (item,) = AsciiFramer().feed(b":" + b"0" * 600)
    assert isinstance(item, DroppedFrame) and "longer than 513" in item.reason


def test_encode_response_faults():
    class Hit:
        action, unit, nbytes = "corrupt_crc", None, None

    good = encode_response("ascii", 0, 1, bytes([3, 2, 0, 5]))
    assert good == AsciiFramer.build(1, bytes([3, 2, 0, 5]))
    bad = encode_response("ascii", 0, 1, bytes([3, 2, 0, 5]), Hit())
    assert len(bad) == len(good) and bad[:-4] == good[:-4] and bad[-2:] == b"\r\n"
    (item,) = AsciiFramer().feed(bad)
    assert isinstance(item, DroppedFrame) and "LRC" in item.reason


# ------------------------------------------------------------------ config
def _listener(**kw):
    cfg = SimConfig.model_validate({"listeners": [{"id": "a", "type": "ascii", "port": "COM7", **kw}], "devices": []})
    return cfg.listeners[0]


def test_ascii_defaults():
    lc = _listener()
    assert (lc.baudrate, lc.bytesize, lc.parity, lc.stopbits, lc.char_timeout, lc.strict_timing) == \
        (19200, 7, "E", 1, 1.0, None)
    assert _listener(parity="N").stopbits == 2
    assert _listener(bytesize=8, char_timeout=0.5).bytesize == 8


@pytest.mark.parametrize("kw, msg", [
    ({"strict_timing": True}, "strict_timing only applies to type rtu"),
])
def test_ascii_invalid(kw, msg):
    with pytest.raises(ValueError, match=msg):
        _listener(**kw)


@pytest.mark.parametrize("listener, msg", [
    ({"id": "r", "type": "rtu", "port": "COM5", "char_timeout": 1}, "char_timeout only applies to type ascii"),
    ({"id": "r", "type": "rtu", "port": "COM5", "bytesize": 7}, "bytesize 8"),
    ({"id": "t", "port": 5021, "char_timeout": 1}, "only apply to serial listeners"),
])
def test_ascii_only_keys(listener, msg):
    with pytest.raises(ValueError, match=msg):
        SimConfig.model_validate({"listeners": [listener], "devices": []})


def test_serial_port_shared_across_types_rejected():
    with pytest.raises(ValueError, match="share the same port"):
        SimConfig.model_validate({"listeners": [{"id": "a", "type": "ascii", "port": "COM7"},
                                                {"id": "r", "type": "rtu", "port": "COM7"}], "devices": []})


# ------------------------------------------------------------------ server
def test_ascii_server_requests_timeout_and_broadcast():
    async def run():
        units = _units()
        events = []
        async with Master() as m:
            srv = ModbusSerialServer(m.url, 19200, 7, "E", 1, resolver=units.get, trace=events.append,
                                     all_units=lambda: units.values(), listener_id="A1",
                                     framer="ascii", char_timeout=0.2)
            await srv.start()
            try:
                await m.connected()
                assert srv.endpoint.endswith("7E1 ascii")
                assert await m.send(AsciiFramer.build(2, bytes.fromhex("0300000002"))) == \
                    AsciiFramer.build(2, bytes([3, 4, 0, 200, 0, 201]))
                # lower-case request, split across writes
                raw = AsciiFramer.build(1, bytes.fromhex("0600030063")).lower()
                assert await m.send(raw[:6], wait=0.05) == b""
                assert await m.send(raw[6:]) == AsciiFramer.build(1, bytes.fromhex("0600030063"))
                assert units[1].get("holding", 3) == [0x63]
                # a gap longer than char_timeout drops the unfinished frame
                assert await m.send(raw[:6], wait=0.4) == b""
                assert await m.send(raw[6:]) == b""
                assert any(e.error == "inter-character timeout" for e in events)
                # bad LRC: no response; exception responses are ASCII too
                assert await m.send(b":010300000001FA\r\n") == b""
                assert await m.send(AsciiFramer.build(1, bytes.fromhex("0300640001"))) == \
                    AsciiFramer.build(1, bytes([0x83, 0x02]))
                # broadcast write: executed, never answered
                assert await m.send(AsciiFramer.build(0, bytes.fromhex("0600070055"))) == b""
                await asyncio.sleep(0.05)
                assert units[2].get("holding", 7) == [0x55]
                assert {e.listener_id for e in events} == {"A1"}
            finally:
                await srv.stop()

    asyncio.run(run())


def test_engine_ascii_listener():
    from sme_modbus_sim.engine import Engine

    async def run():
        async with Master() as m:
            cfg = SimConfig.model_validate({
                "listeners": [{"id": "t", "port": 5020}, {"id": "a", "type": "ascii", "port": m.url}],
                "devices": [{"name": "D", "unit_id": 1, "points": [
                    {"name": "sp", "table": "holding", "address": 0, "value": 5}]}]})
            e = Engine(cfg, listener_overrides={"t": {"port": 0}})
            await e.start()
            try:
                await m.connected()
                lst = {x["id"]: x for x in e.snapshot()["listeners"]}
                assert lst["a"]["status"] == "running" and lst["a"]["host"] is None and lst["a"]["port"] == m.url
                assert await m.send(AsciiFramer.build(1, READ_1)) == AsciiFramer.build(1, bytes([3, 2, 0, 5]))
            finally:
                await e.stop()

    asyncio.run(run())

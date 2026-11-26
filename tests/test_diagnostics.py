"""Serial line diagnostics: FC 07 / 08 / 0B / 0C / 11, counters, event log, Listen Only Mode."""
import asyncio
import struct

import pytest

from sme_modbus_sim.protocol.framer import AsciiFramer, DroppedFrame, Frame, RtuFramer, rtu_request_length
from sme_modbus_sim.protocol.pdu import describe_request, handle_pdu
from sme_modbus_sim.protocol.serial_server import ModbusSerialServer
from sme_modbus_sim.protocol.store import UnitStore

READ = bytes.fromhex("0300000001")


def _units():
    units = {}
    for uid in (1, 2):
        s = UnitStore(uid)
        s.define("holding", 0, 10)
        s.identification = {0: "V", 1: "PROD", 2: "R"}
        units[uid] = s
    return units


class Bus:
    """Drive a serial server's frame handling without a port; collect response PDUs."""

    def __init__(self, framer="rtu", faults=None):
        self.units = _units()
        self.srv = ModbusSerialServer("COM99", resolver=self.units.get, all_units=lambda: self.units.values(),
                                      framer=framer, faults=faults)
        self.srv._serial = object()
        self.sent = []
        self.srv._send = lambda raw, uid, resp, hit=None: self.sent.append((uid, resp))

    def req(self, uid: int, pdu: bytes | str):
        pdu = bytes.fromhex(pdu) if isinstance(pdu, str) else pdu
        before = len(self.sent)
        self.srv._deliver([Frame(unit_id=uid, pdu=pdu, raw=b"")])
        return self.sent[before][1] if len(self.sent) > before else None

    def bad(self):
        self.srv._deliver([DroppedFrame(b"\x01", "CRC mismatch")])

    def count(self, uid: int, sub: int) -> int:
        resp = self.req(uid, bytes([8, 0, sub, 0, 0]))
        assert resp[:3] == bytes([8, 0, sub])
        return struct.unpack(">H", resp[3:5])[0]

    def diag(self, uid=1):
        return self.srv.unit_diagnostics(uid)


# ----------------------------------------------------------- function codes
def test_tcp_handler_rejects_serial_only_fcs():
    store = _units()[1]
    for pdu in ("07", "0800000000", "0B", "0C", "11"):
        assert handle_pdu(store, bytes.fromhex(pdu)) == bytes([0x80 | int(pdu[:2], 16), 0x01])


def test_fc07_fc11():
    b = Bus()
    assert b.req(1, "07") == b"\x07\x00"
    assert b.req(1, "11") == b"\x11\x05PROD\xff"
    assert b.req(1, "0700") == b"\x87\x03"


def test_fc08_return_query_data_any_length():
    b = Bus()
    assert b.req(1, "080000A537") == bytes.fromhex("080000A537")
    assert b.req(1, "0800000102030405") == bytes.fromhex("0800000102030405")
    assert b.req(1, "080000") == bytes.fromhex("080000")


@pytest.mark.parametrize("pdu, code", [
    ("0800", 3),            # no sub-function
    ("0800050000", 3),      # 05-09 reserved -> 03
    ("0800090000", 3),
    ("0800130000", 1),      # 13 and above 14 not supported -> 01
    ("0800150000", 1),
    ("08FFFF0000", 1),
    ("0800020001", 3),      # data must be 0000
    ("08000B", 3),          # data missing
    ("08000100AA", 3),      # restart data not 0000/FF00
    ("0800033A00", 3),      # ':' is not a valid delimiter
    ("0800034101", 3),      # second byte must be 00
])
def test_fc08_invalid(pdu, code):
    assert Bus().req(1, pdu) == bytes([0x88, code])


def test_fc08_diag_register_and_clear_overrun():
    b = Bus()
    assert b.req(1, "0800020000") == bytes.fromhex("0800020000")
    assert b.req(1, "0800140000") == bytes.fromhex("0800140000")


# ----------------------------------------------------------------- counters
def test_counters():
    b = Bus()
    b.req(1, READ)                    # ok
    b.req(1, "0300640001")            # exception 02
    b.req(2, READ)                    # other unit: bus message only for unit 1
    b.req(9, READ)                    # unknown unit: bus message only
    b.bad()                           # CRC error
    b.req(0, "0600010007")            # broadcast write
    b.req(0, READ)                    # broadcast read: not allowed -> CPT3
    assert b.units[1].get("holding", 1) == [7] and b.units[2].get("holding", 1) == [7]
    # counted up to and including the request that reads the counter
    assert b.count(1, 0x0B) == 6 + 1           # bus message (all correct frames)
    assert b.count(1, 0x0C) == 1               # bus communication error
    assert b.count(1, 0x0D) == 2               # exception 02 + broadcast read
    assert b.count(1, 0x0E) == 4 + 4           # 2 own + 2 broadcasts + 4 counter reads
    assert b.count(1, 0x0F) == 2               # the two broadcasts
    assert (b.count(1, 0x10), b.count(1, 0x11), b.count(1, 0x12)) == (0, 0, 0)
    assert b.count(2, 0x0E) == 1 + 2 + 1       # bus counters are shared, unit counters are not
    assert b.count(2, 0x0B) == 6 + 8 + 2


def test_clear_counters_keeps_event_log():
    b = Bus()
    b.req(1, READ)
    b.bad()
    log_before = len(b.diag().events)
    assert b.req(1, "08000A0000") == bytes.fromhex("08000A0000")
    d = b.diag()
    assert (d.server_message, d.event_count, b.srv.bus.bus_message, b.srv.bus.bus_comm_error) == (0, 0, 0, 0)
    assert len(d.events) == log_before + 2  # receive + send events of the 0A request itself
    assert b.count(1, 0x0E) == 1


def test_counters_wrap_at_16_bits():
    b = Bus()
    b.srv.bus.bus_message = 0xFFFF
    assert b.count(1, 0x0B) == 0


def test_busy_and_nak_from_fault_injection():
    class Hit:
        info, delay, action = {"rule": "r"}, None, "exception"

        def __init__(self, code):
            self.code = code

    class Faults:
        codes = [6, 7, None]

        def evaluate(self, *_):
            c = self.codes.pop(0) if self.codes else None
            return Hit(c) if c else None

    b = Bus(faults=Faults())
    assert b.req(1, READ) == b"\x83\x06"
    assert b.req(1, READ) == b"\x83\x07"
    assert (b.count(1, 0x11), b.count(1, 0x10), b.count(1, 0x0D)) == (1, 1, 2)


# ----------------------------------------------------- event counter and log
def test_comm_event_counter_and_log():
    b = Bus()
    assert b.req(1, "0B") == bytes.fromhex("0B00000000")
    b.req(1, READ)
    b.req(1, "0300640001")            # exception: not counted
    b.req(0, "0600010007")            # broadcast completed: counted
    assert b.req(1, "0B") == bytes.fromhex("0B00000002")
    resp = b.req(1, "0C")
    status, events_n, msgs = struct.unpack(">HHH", resp[2:8])
    assert resp[0] == 0x0C and resp[1] == len(resp) - 2 and (status, events_n, msgs) == (0, 2, 6)
    # newest first: receive(0C), send(0B), receive(0B), send(bcast), receive(bcast), send(exc 02) ...
    assert list(resp[8:14]) == [0x80, 0x40, 0x80, 0x40, 0xC0, 0x41]


def test_event_log_depth_64():
    b = Bus()
    for _ in range(40):
        b.req(1, READ)
    resp = b.req(1, "0C")
    assert resp[1] == 6 + 64


# -------------------------------------------------- listen only and restart
def test_listen_only_and_restart():
    b = Bus()
    b.req(1, READ)
    assert b.req(1, "0800040000") is None           # no response
    d = b.diag()
    assert d.listen_only and d.events[0] == 0x04
    assert b.req(1, READ) is None                    # monitored, not answered
    assert b.req(1, "0600010009") is None and b.units[1].get("holding", 1) == [0]  # not executed
    assert b.req(0, "0600010009") is None and b.units[1].get("holding", 1) == [0]  # broadcast neither
    assert b.units[2].get("holding", 1) == [9]
    assert b.req(2, READ) is not None                # the other unit is not affected
    assert d.server_message == 5 and d.events[0] == 0x60   # still counting, listen only bit set
    assert b.req(1, "0800010000") is None            # restart: executed, not answered
    assert not d.listen_only and d.events[0] == 0x00 and d.server_message == 0
    assert len(d.events) > 1                         # 0000 keeps the log
    assert b.req(1, "0800010000") == bytes.fromhex("0800010000")   # answered when not listen only
    assert b.req(1, "080001FF00") == bytes.fromhex("080001FF00")
    assert list(d.events) == [0x00]                  # FF00 clears the log
    assert b.req(1, READ) is not None


# ---------------------------------------------------------- ASCII delimiter
def test_ascii_delimiter_change_and_restart():
    b = Bus(framer="ascii")
    assert b.req(1, "0800032100") == bytes.fromhex("0800032100")
    assert b.srv.delimiter == 0x21
    b.req(1, "0800010000")
    assert b.srv.delimiter == 0x0A
    rtu = Bus()
    assert rtu.req(1, "0800032100") == bytes.fromhex("0800032100")
    assert rtu.srv.delimiter == 0x0A


def test_ascii_framer_delimiter():
    f = AsciiFramer(delimiter=0x21)
    raw = AsciiFramer.build(1, READ)
    assert f.feed(raw) == []                       # CR LF no longer ends the frame
    dropped, item = f.feed(AsciiFramer.build(1, READ, delimiter=0x21))
    assert isinstance(dropped, DroppedFrame) and isinstance(item, Frame) and item.pdu == READ


# ------------------------------------------------------------------ framing
@pytest.mark.parametrize("hexs, n", [
    ("0107", 4), ("010B", 4), ("010C", 4), ("0111", 4),
    ("0108", None), ("010800", None), ("0108000A", 8), ("01080000", -1),
])
def test_rtu_request_length(hexs, n):
    assert rtu_request_length(bytes.fromhex(hexs)) == n


def test_describe():
    assert describe_request(bytes.fromhex("08000A0000")) == "FC08 diagnostics sub=0A clear counters data=0000"
    assert describe_request(bytes.fromhex("0B")) == "FC0B get comm event counter"


serial = pytest.importorskip("serial")


def test_serial_rtu_and_ascii_end_to_end():
    from test_serial import Master

    async def run(framer, build, delimiter_test):
        units = _units()
        async with Master() as m:
            srv = ModbusSerialServer(m.url, 19200, 8 if framer == "rtu" else 7, "E", 1, resolver=units.get,
                                     all_units=lambda: units.values(), framer=framer, char_timeout=0.2)
            await srv.start()
            try:
                await m.connected()
                assert await m.send(build(1, b"\x07")) == build(1, b"\x07\x00")
                assert await m.send(build(1, bytes.fromhex("080000A537"))) == build(1, bytes.fromhex("080000A537"))
                assert await m.send(build(1, b"\x0b")) == build(1, bytes.fromhex("0B00000002"))
                assert await m.send(b"\x01\x03\x00\x00\x00\x01\x00\x00" if framer == "rtu"
                                    else b":010300000001FA\r\n") == b""
                assert await m.send(build(1, bytes.fromhex("08000C0000"))) == build(1, bytes.fromhex("08000C0001"))
                if delimiter_test:
                    assert await m.send(build(1, bytes.fromhex("0800032100"))) == build(1, bytes.fromhex("0800032100"))
                    assert await m.send(AsciiFramer.build(1, b"\x07", delimiter=0x21)) == build(1, b"\x07\x00")
            finally:
                await srv.stop()

    asyncio.run(run("rtu", RtuFramer.build, False))
    asyncio.run(run("ascii", AsciiFramer.build, True))

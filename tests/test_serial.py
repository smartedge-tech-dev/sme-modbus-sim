"""Serial RTU listener: real frames through pyserial's ``socket://`` URL handler.

The test acts as the Modbus master on the TCP side of a local socket; the server opens
``socket://127.0.0.1:<port>`` exactly like it would open ``COM5``.
"""
import asyncio
import socket
import struct

import pytest

serial = pytest.importorskip("serial")

from sme_modbus_sim.config import SimConfig  # noqa: E402
from sme_modbus_sim.engine import Engine  # noqa: E402
from sme_modbus_sim.protocol.framer import RtuFramer  # noqa: E402
from sme_modbus_sim.protocol.serial_server import ModbusSerialServer, t15, t35  # noqa: E402
from sme_modbus_sim.protocol.store import UnitStore  # noqa: E402


def _units():
    units = {}
    for uid in (1, 2):
        s = UnitStore(uid)
        s.define("holding", 0, 20)
        s.set("holding", 0, [uid * 100 + i for i in range(20)])
        s.identification = {0: "V", 1: "P", 2: "R"}
        units[uid] = s
    return units


class Master:
    """TCP endpoint the serial server connects to via socket://."""

    async def __aenter__(self):
        self._conn = asyncio.get_running_loop().create_future()
        self._srv = await asyncio.start_server(self._on, "127.0.0.1", 0)
        self.url = f"socket://127.0.0.1:{self._srv.sockets[0].getsockname()[1]}"
        return self

    def _on(self, r, w):
        if not self._conn.done():
            self._conn.set_result((r, w))

    async def connected(self):
        self.reader, self.writer = await asyncio.wait_for(self._conn, 2)

    async def send(self, raw: bytes, wait: float = 0.3) -> bytes:
        self.writer.write(raw)
        await self.writer.drain()
        try:
            return await asyncio.wait_for(self.reader.read(512), wait)
        except asyncio.TimeoutError:
            return b""

    async def __aexit__(self, *exc):
        if hasattr(self, "writer"):
            self.writer.close()
        self._srv.close()


def _closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"socket://127.0.0.1:{port}"


def test_timing_constants():
    assert t35(9600) == pytest.approx(3.5 * 11 / 9600)
    assert t15(9600) == pytest.approx(1.5 * 11 / 9600)
    assert t35(19200) == pytest.approx(3.5 * 11 / 19200)
    assert (t15(38400), t35(115200)) == (0.00075, 0.00175)


def test_serial_requests_broadcast_and_addressing():
    async def run():
        units = _units()
        events = []
        async with Master() as m:
            srv = ModbusSerialServer(m.url, 19200, resolver=units.get, trace=events.append,
                                     all_units=lambda: units.values(), listener_id="S1")
            await srv.start()
            try:
                await m.connected()
                assert (srv.status, srv.client_count, srv.error) == ("running", 1, None)
                assert await m.send(RtuFramer.build(2, bytes.fromhex("0300000002"))) == \
                    RtuFramer.build(2, bytes([3, 4, 0, 200, 0, 201]))
                # FC17 write-then-read and FC2B/0E over the serial line
                req = bytes([0x17]) + struct.pack(">HHHHB", 0, 2, 1, 1, 2) + struct.pack(">H", 0x1234)
                assert await m.send(RtuFramer.build(1, req)) == \
                    RtuFramer.build(1, bytes([0x17, 4, 0, 100, 0x12, 0x34]))
                resp = await m.send(RtuFramer.build(1, bytes.fromhex("2B0E0100")))
                assert resp[1:4] == bytes([0x2B, 0x0E, 0x01])
                # broadcast write: executed on every unit, never answered
                assert await m.send(RtuFramer.build(0, bytes.fromhex("0600070055"))) == b""
                await asyncio.sleep(0.05)
                assert units[1].get("holding", 7) == [0x55] and units[2].get("holding", 7) == [0x55]
                # broadcast read: ignored; reserved 248..255 and unknown units: ignored
                assert await m.send(RtuFramer.build(0, bytes.fromhex("0300000001"))) == b""
                assert await m.send(RtuFramer.build(248, bytes.fromhex("0300000001"))) == b""
                assert await m.send(RtuFramer.build(9, bytes.fromhex("0300000001"))) == b""
                # corrupted CRC: dropped, no answer
                bad = bytearray(RtuFramer.build(1, bytes.fromhex("0300000001")))
                bad[-1] ^= 0xFF
                assert await m.send(bytes(bad)) == b""
                assert any(e.error == "CRC mismatch" for e in events)
                # unknown-length FC: delimited by silence, answered with exception 01
                assert await m.send(RtuFramer.build(1, bytes.fromhex("2B0D0100"))) == \
                    RtuFramer.build(1, bytes([0xAB, 0x01]))
                assert {e.listener_id for e in events} == {"S1"} and events[0].peer == m.url
            finally:
                await srv.stop()
            assert (srv.status, srv.client_count) == ("stopped", 0)

    asyncio.run(run())


@pytest.mark.parametrize("strict, answered", [(True, False), (False, True)])
def test_inter_character_gap(strict, answered):
    """300 baud: t1.5 = 55 ms, t3.5 = 128 ms; a 90 ms gap violates t1.5 only."""
    async def run():
        units = _units()
        events = []
        async with Master() as m:
            srv = ModbusSerialServer(m.url, 300, resolver=units.get, trace=events.append, strict_timing=strict)
            await srv.start()
            try:
                await m.connected()
                frame = RtuFramer.build(1, bytes.fromhex("0300000001"))
                m.writer.write(frame[:4])
                await m.writer.drain()
                await asyncio.sleep(0.09)
                resp = await m.send(frame[4:], wait=0.5)
                assert bool(resp) is answered
                if strict:
                    assert any(e.error and "t1.5" in e.error for e in events)
            finally:
                await srv.stop()

    asyncio.run(run())


def test_open_error_is_reported_not_raised():
    async def run():
        srv = ModbusSerialServer(_closed_port_url())
        await srv.start()
        assert srv.status == "error" and "cannot open serial port" in srv.error and srv.client_count == 0
        await srv.stop()

    asyncio.run(run())


def _cfg(serial_url, **point):
    return SimConfig.model_validate({
        "listeners": [{"id": "t", "port": 5020}, {"id": "s", "type": "rtu", "port": serial_url}],
        "devices": [{"name": "D", "unit_id": 1, "points": [
            {"name": "sp", "table": "holding", "address": 0, "value": 5, "min": 0, "max": 100, **point}]}],
    })


def test_engine_serial_listener_and_reload():
    async def run():
        async with Master() as m:
            e = Engine(_cfg(m.url), listener_overrides={"t": {"port": 0}})
            await e.start()
            try:
                await m.connected()
                st = e.state()
                lst = {x["id"]: x for x in st["listeners"]}
                assert lst["s"]["status"] == "running" and lst["s"]["clients"] == 1 and lst["s"]["port"] == m.url
                assert lst["t"]["status"] == "running"
                assert await m.send(RtuFramer.build(1, bytes.fromhex("0300000001"))) == \
                    RtuFramer.build(1, bytes([3, 2, 0, 5]))
                # FC16 through the engine validators: result 0x00FF > max 100 -> exception 03
                assert await m.send(RtuFramer.build(1, bytes.fromhex("160000000000FF"))) == \
                    RtuFramer.build(1, bytes([0x96, 0x03]))
                m.writer.close()
                m._conn = asyncio.get_running_loop().create_future()
                await e.reload(_cfg(m.url))  # port closed and reopened
                await m.connected()
                assert await m.send(RtuFramer.build(1, bytes.fromhex("0300000001"))) == \
                    RtuFramer.build(1, bytes([3, 2, 0, 5]))
            finally:
                await e.stop()

    asyncio.run(run())


def test_engine_keeps_running_when_serial_port_fails():
    async def run():
        e = Engine(_cfg(_closed_port_url()), listener_overrides={"t": {"port": 0}})
        await e.start()
        try:
            lst = {x["id"]: x for x in e.state()["listeners"]}
            assert lst["s"]["status"] == "error" and "cannot open" in lst["s"]["error"]
            assert lst["t"]["status"] == "running" and lst["s"]["clients"] == 0
            assert any(ev["level"] == "error" and "listener s" in ev["message"] for ev in e.state()["events"])
        finally:
            await e.stop()

    asyncio.run(run())

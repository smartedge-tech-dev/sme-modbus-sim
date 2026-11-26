"""Modbus UDP listener (type: udp) and the udp transport of the read-only client."""

import asyncio

import pytest
from pymodbus.client import AsyncModbusUdpClient

from sme_modbus_sim.config import parse_text
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.faults import FaultManager
from sme_modbus_sim.protocol.client import ModbusClient, ResponseTimeout
from sme_modbus_sim.protocol.framer import MbapFramer
from sme_modbus_sim.protocol.server import ModbusUdpServer
from sme_modbus_sim.scan import make_client
from tests.test_protocol_server import make_units

READ0 = bytes([3, 0, 0, 0, 2])


def make_udp(units, **kw):
    events = []
    kw.setdefault("default_unit", 1)
    srv = ModbusUdpServer("127.0.0.1", 0, resolver=units.get, trace=events.append,
                          all_units=lambda: units.values(), listener_id="U1", **kw)
    return srv, events


class _Recv(asyncio.DatagramProtocol):
    def __init__(self):
        self.q = asyncio.Queue()

    def datagram_received(self, data, addr):
        self.q.put_nowait(data)


async def udp_xfer(port, payload, timeout=0.3):
    """Send one datagram, return the answer or None."""
    tr, proto = await asyncio.get_running_loop().create_datagram_endpoint(
        _Recv, remote_addr=("127.0.0.1", port))
    try:
        tr.sendto(payload)
        try:
            return await asyncio.wait_for(proto.q.get(), timeout)
        except asyncio.TimeoutError:
            return None
    finally:
        tr.close()


def run_srv(body, **kw):
    async def main():
        units = make_units()
        srv, events = make_udp(units, **kw)
        await srv.start()
        try:
            await body(srv, events, units)
        finally:
            await srv.stop()
    asyncio.run(main())


def test_pymodbus_udp_read_write():
    async def body(srv, events, units):
        client = AsyncModbusUdpClient("127.0.0.1", port=srv.bound_port, timeout=1, retries=0)
        try:
            await client.connect()
            rr = await client.read_holding_registers(0, count=3, device_id=1)
            assert rr.registers == [100, 101, 102]
            rr = await client.read_holding_registers(0, count=1, device_id=2)
            assert rr.registers == [200]
            wr = await client.write_register(5, 1234, device_id=1)
            assert not wr.isError()
            assert units[1].get("holding", 5, 1) == [1234]
        finally:
            client.close()
        assert srv.client_count == 1
        assert events[0].peer.startswith("127.0.0.1:")
    run_srv(body)


def test_transaction_id_echoed_and_peers_independent():
    async def body(srv, events, units):
        r = await udp_xfer(srv.bound_port, MbapFramer.build(0xBEEF, 1, READ0))
        assert r == MbapFramer.build(0xBEEF, 1, bytes([3, 4, 0, 100, 0, 101]))
        r2 = await udp_xfer(srv.bound_port, MbapFramer.build(7, 2, READ0))
        assert r2[:2] == b"\x00\x07" and r2[7:] == bytes([3, 4, 0, 200, 0, 201])
        assert srv.client_count == 2  # two source ports
    run_srv(body)


@pytest.mark.parametrize("payload, reason", [
    (b"\x00\x01\x00\x00\x00", "too short"),
    (MbapFramer.build(1, 1, READ0) + b"\x00", "does not match"),
    (MbapFramer.build(1, 1, READ0)[:-1], "does not match"),
    (b"\x00\x01\x00\x05" + MbapFramer.build(1, 1, READ0)[4:], "protocol id"),
])
def test_malformed_datagram_dropped(payload, reason):
    async def body(srv, events, units):
        assert await udp_xfer(srv.bound_port, payload) is None
        assert events[-1].summary == "dropped" and reason in events[-1].error
        # the listener keeps serving
        assert await udp_xfer(srv.bound_port, MbapFramer.build(2, 1, READ0)) is not None
    run_srv(body)


def test_unknown_unit_policies():
    async def ignore(srv, events, units):
        assert await udp_xfer(srv.bound_port, MbapFramer.build(1, 9, READ0)) is None
        # unit 0 / 255 are aliases for default_unit, as on TCP
        assert (await udp_xfer(srv.bound_port, MbapFramer.build(1, 255, READ0)))[7:] == bytes([3, 4, 0, 100, 0, 101])
    run_srv(ignore)

    async def exception(srv, events, units):
        r = await udp_xfer(srv.bound_port, MbapFramer.build(1, 9, READ0))
        assert r[7:] == bytes([0x83, 0x0B])
    run_srv(exception, unknown_unit="exception")


def test_faults_on_udp():
    async def body(srv, events, units):
        fm = FaultManager()
        srv.faults = fm
        port = srv.bound_port
        fm.add({"id": "nr", "action": "no_response"})
        assert await udp_xfer(port, MbapFramer.build(1, 1, READ0)) is None
        fm.clear()
        fm.add({"id": "dc", "action": "disconnect"})  # no connection to close: left unanswered
        assert await udp_xfer(port, MbapFramer.build(1, 1, READ0)) is None
        fm.clear()
        fm.add({"id": "ex", "action": "exception", "params": {"code": 4}})
        assert (await udp_xfer(port, MbapFramer.build(1, 1, READ0)))[7:] == bytes([0x83, 0x04])
        fm.clear()
        fm.add({"id": "tid", "action": "wrong_transaction_id"})
        assert (await udp_xfer(port, MbapFramer.build(1, 1, READ0)))[:2] == b"\x00\x02"
        fm.clear()
        fm.add({"id": "dl", "action": "delay", "params": {"ms": 200}})
        assert await udp_xfer(port, MbapFramer.build(1, 1, READ0), timeout=0.1) is None
        assert await udp_xfer(port, MbapFramer.build(1, 1, READ0), timeout=0.5) is not None
        fm.clear()
        fm.add({"id": "rf", "action": "refuse_connections"})
        assert await udp_xfer(port, MbapFramer.build(1, 1, READ0)) is None
        assert any(e.error == "datagram refused (fault)" for e in events), [(e.direction, e.summary, e.error, e.fault) for e in events[-4:]]
        fm.clear()
        assert await udp_xfer(port, MbapFramer.build(1, 1, READ0)) is not None
    run_srv(body)


def test_read_only_client_udp():
    async def body(srv, events, units):
        async with make_client({"type": "udp", "host": "127.0.0.1", "port": srv.bound_port},
                               timeout=0.5, rate=1000) as c:
            assert c.endpoint.startswith("udp 127.0.0.1:")
            assert await c.read_holding_registers(1, 0, 3) == [100, 101, 102]
            assert await c.read_input_registers(2, 0, 2) == [2, 3]
            assert await c.read_discrete_inputs(1, 0, 3) == [1, 0, 1]
            with pytest.raises(ResponseTimeout):
                await c.read_holding_registers(9, 0, 1)  # unknown unit: no answer
            assert await c.read_holding_registers(1, 1, 1) == [101]
    run_srv(body)


def test_client_udp_nobody_listening_times_out():
    async def main():
        srv, _ = make_udp(make_units())
        await srv.start()
        port = srv.bound_port
        await srv.stop()
        async with ModbusClient("udp", "127.0.0.1", port, timeout=0.2) as c:
            with pytest.raises(ResponseTimeout):
                await c.read_holding_registers(1, 0, 1)
    asyncio.run(main())


CONFIG = """
listeners:
  - {id: t1, type: tcp, host: 127.0.0.1, port: 15030}
  - {id: u1, type: udp, host: 127.0.0.1, port: 15030}
devices:
  - name: D
    unit_id: 1
    points:
      - {name: sp, table: holding, address: 0, datatype: int16, value: 5}
"""


def test_config_udp_and_tcp_may_share_a_port():
    cfg = parse_text(CONFIG)
    assert [l.type for l in cfg.listeners] == ["tcp", "udp"]
    with pytest.raises(ValueError, match="15030/udp"):
        parse_text(CONFIG.replace("{id: t1, type: tcp", "{id: t1, type: udp"))
    with pytest.raises(ValueError, match="UDP port"):
        parse_text(CONFIG.replace("port: 15030}\ndevices", "port: 0}\ndevices"))
    with pytest.raises(ValueError, match="only apply to serial"):
        parse_text(CONFIG.replace("{id: u1, type: udp,", "{id: u1, type: udp, baudrate: 9600,"))


def test_engine_serves_udp_listener():
    async def main():
        eng = Engine(parse_text(CONFIG), listener_overrides={"t1": {"port": 0}, "u1": {"port": 0}})
        await eng.start()
        try:
            port = eng.servers["u1"].bound_port
            r = await udp_xfer(port, MbapFramer.build(3, 1, bytes([3, 0, 0, 0, 1])))
            assert r[7:] == bytes([3, 2, 0, 5])
            snap = eng.stats_snapshot()["u1"]
            assert snap["type"] == "udp" and snap["status"] == "running" and snap["clients"] == 1
        finally:
            await eng.stop()
    asyncio.run(main())


def test_answer_to_departed_client_does_not_stall_listener():
    # Windows: the ICMP port unreachable for the late answer must not lose the next datagram
    async def body(srv, events, units):
        fm = FaultManager()
        srv.faults = fm
        fm.add({"id": "dl", "action": "delay", "params": {"ms": 100}})
        assert await udp_xfer(srv.bound_port, MbapFramer.build(1, 1, READ0), timeout=0.02) is None
        await asyncio.sleep(0.2)  # late answer sent to the closed client port
        fm.clear()
        for tid in (2, 3):
            assert (await udp_xfer(srv.bound_port, MbapFramer.build(tid, 1, READ0)))[:2] == tid.to_bytes(2, "big")
    run_srv(body)

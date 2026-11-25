import asyncio

from pymodbus.client import AsyncModbusTcpClient

from sme_modbus_sim.protocol.framer import MbapFramer, RtuFramer
from sme_modbus_sim.protocol.server import ModbusTcpServer
from sme_modbus_sim.protocol.store import UnitStore


def make_units():
    units = {}
    for uid in (1, 2):
        s = UnitStore(uid)
        s.define("coil", 0, 16)
        s.define("discrete", 0, 16)
        s.define("holding", 0, 100)
        s.define("input", 0, 100)
        s.set("holding", 0, [uid * 100 + i for i in range(10)])
        s.set("input", 0, [uid, uid + 1])
        s.set("discrete", 0, [1, 0, 1])
        units[uid] = s
    return units


def make_server(units, **kw):
    events = []
    kw.setdefault("default_unit", 1)
    srv = ModbusTcpServer(
        "127.0.0.1", 0, resolver=units.get, trace=events.append,
        all_units=lambda: units.values(), listener_id="L1", **kw,
    )
    return srv, events


async def _raw_exchange(port, payload, read=True, timeout=0.5):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(payload)
        await writer.drain()
        if not read:
            await asyncio.sleep(0.2)
            return None
        try:
            return await asyncio.wait_for(reader.read(1024), timeout)
        except asyncio.TimeoutError:
            return b""
    finally:
        writer.close()


def test_pymodbus_tcp_all_fcs():
    async def run():
        units = make_units()
        srv, events = make_server(units)
        await srv.start()
        client = AsyncModbusTcpClient("127.0.0.1", port=srv.bound_port, timeout=2, retries=0)
        try:
            assert await client.connect()
            rr = await client.read_holding_registers(0, count=3, device_id=1)
            assert rr.registers == [100, 101, 102]
            rr = await client.read_holding_registers(0, count=2, device_id=2)
            assert rr.registers == [200, 201]
            rr = await client.read_input_registers(0, count=2, device_id=2)
            assert rr.registers == [2, 3]
            rr = await client.read_discrete_inputs(0, count=3, device_id=1)
            assert rr.bits[:3] == [True, False, True]
            await client.write_coil(4, True, device_id=1)
            rr = await client.read_coils(0, count=8, device_id=1)
            assert rr.bits[4] is True
            await client.write_coils(0, [True, False, True], device_id=1)
            assert units[1].get("coil", 0, 3) == [1, 0, 1]
            await client.write_register(50, 4242, device_id=1)
            await client.write_registers(60, [1, 2, 3], device_id=1)
            assert units[1].get("holding", 50) == [4242]
            assert units[1].get("holding", 60, 3) == [1, 2, 3]
            rr = await client.read_holding_registers(99, count=2, device_id=1)
            assert rr.isError() and rr.exception_code == 2
            # unit 255 / 0 alias default unit on TCP
            rr = await client.read_holding_registers(0, count=1, device_id=255)
            assert rr.registers == [100]
        finally:
            client.close()
            await srv.stop()
        rx = [e for e in events if e.direction == "rx"]
        tx = [e for e in events if e.direction == "tx"]
        assert rx and tx and all(e.listener_id == "L1" for e in events)
        assert any(e.exception_code == 2 for e in tx)

    asyncio.run(run())


def test_tcp_unknown_unit_policies():
    async def run():
        units = make_units()
        pdu = bytes.fromhex("0300000001")
        srv, _ = make_server(units)
        await srv.start()
        try:
            resp = await _raw_exchange(srv.bound_port, MbapFramer.build(7, 9, pdu))
            assert resp == b""  # ignored
        finally:
            await srv.stop()
        srv, _ = make_server(units, unknown_unit="exception")
        await srv.start()
        try:
            resp = await _raw_exchange(srv.bound_port, MbapFramer.build(7, 9, pdu))
            assert resp == MbapFramer.build(7, 9, bytes([0x83, 0x0B]))
            # protocol id != 0 silently discarded
            bad = bytes.fromhex("000700010006") + bytes([1]) + pdu
            assert await _raw_exchange(srv.bound_port, bad) == b""
        finally:
            await srv.stop()
        srv, _ = make_server(units, unit_id_mode="any")
        await srv.start()
        try:
            resp = await _raw_exchange(srv.bound_port, MbapFramer.build(1, 42, pdu))
            assert resp == MbapFramer.build(1, 42, bytes([3, 2, 0, 100]))
        finally:
            await srv.stop()

    asyncio.run(run())


def test_rtu_over_tcp_raw_socket():
    async def run():
        units = make_units()
        srv, events = make_server(units, framer="rtu_over_tcp")
        await srv.start()
        port = srv.bound_port
        try:
            resp = await _raw_exchange(port, RtuFramer.build(2, bytes.fromhex("0300000002")))
            assert resp == RtuFramer.build(2, bytes([3, 4, 0, 200, 0, 201]))
            # bad CRC -> no response
            bad = bytearray(RtuFramer.build(1, bytes.fromhex("0300000001")))
            bad[-1] ^= 0xFF
            assert await _raw_exchange(port, bytes(bad)) == b""
            # unknown unit -> no response
            assert await _raw_exchange(port, RtuFramer.build(9, bytes.fromhex("0300000001"))) == b""
            # unknown FC -> flushed on idle -> exception 01
            resp = await _raw_exchange(port, RtuFramer.build(1, bytes.fromhex("2B0D0100")))
            assert resp == RtuFramer.build(1, bytes([0xAB, 0x01]))
            # broadcast write -> executed on all units, no response
            await _raw_exchange(port, RtuFramer.build(0, bytes.fromhex("0600070055")), read=False)
            assert units[1].get("holding", 7) == [0x55] and units[2].get("holding", 7) == [0x55]
            # FC10 split across writes
            frame = RtuFramer.build(1, bytes.fromhex("100010000204AAAABBBB"))
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(frame[:5])
            await writer.drain()
            await asyncio.sleep(0.01)
            writer.write(frame[5:])
            resp = await asyncio.wait_for(reader.read(1024), 1)
            writer.close()
            assert resp == RtuFramer.build(1, bytes.fromhex("1000100002"))
            assert units[1].get("holding", 16, 2) == [0xAAAA, 0xBBBB]
        finally:
            await srv.stop()
        assert any(e.error == "CRC mismatch" for e in events)

    asyncio.run(run())


def test_many_concurrent_clients_and_stop():
    async def run():
        units = make_units()
        srv, _ = make_server(units)
        await srv.start()
        port = srv.bound_port

        async def one(i):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            for k in range(5):
                writer.write(MbapFramer.build(i * 10 + k, 1, bytes.fromhex("0300000001")))
                await writer.drain()
                data = await asyncio.wait_for(reader.readexactly(11), 2)
                assert data == MbapFramer.build(i * 10 + k, 1, bytes([3, 2, 0, 100]))
            return reader, writer

        conns = await asyncio.gather(*(one(i) for i in range(30)))
        assert srv.client_count == 30
        await srv.stop()
        for reader, writer in conns:
            assert await asyncio.wait_for(reader.read(10), 2) == b""
            writer.close()

    asyncio.run(run())

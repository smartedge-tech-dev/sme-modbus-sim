"""End to end: demo config served on ephemeral ports, read / written with pymodbus 3.16.2 as the master."""

import asyncio
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.framer import FramerType

from sme_modbus_sim.config import load_file
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.protocol import datatypes as dt

DEMO = Path(__file__).resolve().parent.parent / "configs" / "demo-site.yaml"


def _engine() -> Engine:
    cfg = load_file(DEMO)
    cfg.simulation.tick_seconds = 0.1
    cfg.simulation.time_scale = 10  # 1 simulated second per tick
    local = {"host": "127.0.0.1", "port": 0}
    return Engine(cfg, DEMO.parent, listener_overrides={"tcp1": local, "gw1": dict(local)})


async def _wait_for(pred, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await pred():
            return True
        await asyncio.sleep(0.05)
    return False


def test_tcp_read_write_and_ramp():
    async def main():
        eng = _engine()
        await eng.start()
        client = AsyncModbusTcpClient("127.0.0.1", port=eng.servers["tcp1"].bound_port, timeout=2, retries=0)
        try:
            assert await client.connect()
            # meter (unit 3): float32 CDAB voltage around 230 V
            rr = await client.read_input_registers(0, count=2, device_id=3)
            assert not rr.isError()
            assert 220 < dt.decode(rr.registers, "float32", word_order="little") < 240
            rr = await client.read_holding_registers(0, count=8, device_id=3)
            assert dt.decode(rr.registers, "string", count=8) == "MTR00001"
            # PCS (unit 1): run + 20 kW discharge setpoint
            assert not (await client.write_coil(0, True, device_id=1)).isError()
            assert not (await client.write_register(0, 200, device_id=1)).isError()
            rr = await client.read_holding_registers(0, count=1, device_id=1)
            assert rr.registers == [200]
            assert eng.devices["PCS-1"].by_name["p_setpoint"].value == 20.0

            ramp_seen = set()

            async def ramped():
                r = await client.read_input_registers(0, count=2, device_id=1)  # state, p_actual
                ramp_seen.add(r.registers[1])
                return r.registers == [2, 200]

            assert await _wait_for(ramped), ramp_seen
            # out of range setpoint -> 03, read-only point (Meter-1 serial_number) -> 02
            resp = await client.write_register(0, 9999, device_id=1)
            assert resp.isError() and resp.exception_code == 3
            resp = await client.write_register(0, 1, device_id=3)
            assert resp.isError() and resp.exception_code == 2
            stats = eng.stats_snapshot()["tcp1"]
            assert stats["requests"].get("06", 0) >= 3 and stats["exceptions"].get("03") == 1
            assert any(e["function_code"] == 6 for e in eng.trace(50, unit=1))
        finally:
            client.close()
            await eng.stop()

    asyncio.run(main())


def test_ramp_is_slew_limited():
    eng = _engine()
    pcs = eng.devices["PCS-1"]
    eng.set_point("PCS-1", "run_command", True)
    eng.set_point("PCS-1", "p_setpoint", 40)
    eng.step(1.0)  # state -> running (evaluated before p_actual in the same tick)
    assert pcs.by_name["p_actual"].read() == 10.0  # ramp_kw_per_s = 10
    eng.step(1.0)
    assert pcs.by_name["p_actual"].read() == 20.0
    for _ in range(5):
        eng.step(1.0)
    assert pcs.by_name["p_actual"].read() == 40.0


def test_rtu_over_tcp_gateway():
    async def main():
        eng = _engine()
        await eng.start()
        client = AsyncModbusTcpClient("127.0.0.1", port=eng.servers["gw1"].bound_port, framer=FramerType.RTU,
                                      timeout=2, retries=0)
        try:
            assert await client.connect()
            rr = await client.read_input_registers(0, count=2, device_id=4)  # Env-1 temperature, humidity
            assert not rr.isError()
            assert 15 < dt.decode(rr.registers[:1], "int16", scale=0.1) < 35
            rr = await client.read_holding_registers(0, count=1, device_id=4)  # Env-1 mode
            assert rr.registers == [2]
            assert not (await client.write_register(0, 1, device_id=4)).isError()
            assert eng.devices["Env-1"].by_name["mode"].value == 1
            rr = await client.read_input_registers(0, count=1, device_id=2)  # BMS soc via the same gateway
            assert rr.registers[0] == 600
            assert eng.stats_snapshot()["gw1"]["requests"]["04"] >= 2
        finally:
            client.close()
            await eng.stop()

    asyncio.run(main())

"""Fault injection (design 5.4): rule model, every action end to end, API / WS / presets."""

import asyncio
import json
import random
import time

import pytest
from fastapi.testclient import TestClient
from pymodbus.client import AsyncModbusTcpClient

from sme_modbus_sim.config import parse_text
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.faults import FaultManager
from sme_modbus_sim.protocol.framer import MbapFramer, RtuFramer
from sme_modbus_sim.web import create_app

CONFIG = """
simulation: {tick_seconds: 0.1, time_scale: 1}
listeners:
  - {id: tcp1, type: tcp, host: 127.0.0.1, port: 15020}
  - {id: rtu1, type: rtu_over_tcp, host: 127.0.0.1, port: 15021}
devices:
  - name: D
    unit_id: 1
    listeners: [tcp1, rtu1]
    points:
      - {name: sp, table: holding, address: 0, datatype: int16, value: 5}
      - {name: ramp, table: holding, address: 10, datatype: float32, behavior: {type: expr, expr: t}}
"""
LOCAL = {"host": "127.0.0.1", "port": 0}
READ0 = bytes([3, 0, 0, 0, 1])      # read holding 0, qty 1
READ10 = bytes([3, 0, 10, 0, 2])    # read holding 10, qty 2


def make_engine() -> Engine:
    return Engine(parse_text(CONFIG), listener_overrides={"tcp1": dict(LOCAL), "rtu1": dict(LOCAL)})


def run(body):
    async def main():
        eng = make_engine()
        await eng.start()
        try:
            await body(eng, eng.servers["tcp1"].bound_port, eng.servers["rtu1"].bound_port)
        finally:
            await eng.stop()
    asyncio.run(main())


async def xfer(port, data, timeout=0.5):
    """Send one ADU on a fresh connection; return the reply, None on timeout, b'' on close."""
    r, w = await asyncio.open_connection("127.0.0.1", port)
    try:
        w.write(data)
        await w.drain()
        try:
            return await asyncio.wait_for(r.read(300), timeout)
        except asyncio.TimeoutError:
            return None
    except ConnectionError:   # refused / reset (Windows reports WinError 64)
        return b""
    finally:
        w.close()


def tcp(pdu, tid=7, unit=1):
    return MbapFramer.build(tid, unit, pdu)


# ------------------------------------------------------------------ rule model / gating


def test_spec_validation():
    fm = FaultManager()
    with pytest.raises(ValueError):
        fm.add({"action": "exception", "params": {"code": 7}})
    with pytest.raises(ValueError):
        fm.add({"action": "delay"})
    with pytest.raises(ValueError):
        fm.add({"action": "busy", "params": {"ms": 1}})
    with pytest.raises(ValueError):
        fm.add({"action": "nope"})
    r = fm.add({"action": "exception", "params": {"code": "0A"}, "address_start": 5})
    assert r["id"] == "f1" and r["params"] == {"code": 10}
    assert fm.add({"action": "busy"})["params"] == {"seconds": 10.0}
    with pytest.raises(ValueError):
        fm.add({"id": "f1", "action": "no_response"})


def test_every_n_remaining_probability_and_fallthrough():
    fm = FaultManager(rng=random.Random(1))
    fm.add({"id": "ev", "action": "exception", "params": {"code": 4}, "every_n": 3})
    fm.add({"id": "rest", "action": "no_response", "remaining": 2})
    hits = [fm.evaluate("l", "tcp", 1, READ0) for _ in range(6)]
    assert [h and h.rule for h in hits] == ["rest", "rest", "ev", None, None, "ev"]
    assert fm.list()[1]["enabled"] is False and fm.list()[1]["remaining"] == 0
    assert fm.list()[0]["triggers"] == 2

    fm.load([{"id": "p0", "action": "no_response", "probability": 0}])
    assert all(fm.evaluate("l", "tcp", 1, READ0) is None for _ in range(50))
    fm.load([{"id": "p5", "action": "no_response", "probability": 0.5}])
    n = sum(fm.evaluate("l", "tcp", 1, READ0) is not None for _ in range(400))
    assert 140 < n < 260


def test_filters_and_framer_applicability():
    fm = FaultManager()
    fm.add({"action": "exception", "listener": "a", "unit": 2, "function_codes": [3], "address_start": 4, "address_end": 6})
    assert fm.evaluate("a", "tcp", 2, bytes([3, 0, 0, 0, 4])) is None          # 0..3
    assert fm.evaluate("a", "tcp", 2, bytes([3, 0, 3, 0, 2])) is not None      # 3..4 overlaps
    assert fm.evaluate("b", "tcp", 2, bytes([3, 0, 4, 0, 1])) is None          # listener
    assert fm.evaluate("a", "tcp", 1, bytes([3, 0, 4, 0, 1])) is None          # unit
    assert fm.evaluate("a", "tcp", 2, bytes([4, 0, 4, 0, 1])) is None          # FC
    fm.load([{"action": "corrupt_crc"}, {"action": "wrong_transaction_id"}])
    assert fm.evaluate("a", "tcp", 1, READ0).action == "wrong_transaction_id"
    assert fm.evaluate("a", "rtu", 1, READ0).action == "corrupt_crc"


# ------------------------------------------------------------------ end to end


def test_exception_pymodbus_and_trace():
    async def body(eng, tport, _rport):
        rid = eng.faults.add({"action": "exception", "params": {"code": 4}, "function_codes": [3]})["id"]
        c = AsyncModbusTcpClient("127.0.0.1", port=tport, timeout=1, retries=0)
        assert await c.connect()
        try:
            rr = await c.read_holding_registers(0, count=1, device_id=1)
            assert rr.isError() and rr.exception_code == 4
            assert not (await c.write_register(0, 9, device_id=1)).isError()
        finally:
            c.close()
        assert eng.devices["D"].by_name["sp"].value == 9
        faulted = [e for e in eng.trace(50) if e.get("fault")]
        assert {e["direction"] for e in faulted} == {"rx", "tx"}
        assert faulted[0]["fault"] == {"rule": rid, "action": "exception"}
    run(body)


def test_delay_does_not_block_other_connections():
    async def body(eng, tport, _rport):
        eng.faults.add({"action": "delay", "params": {"ms": 600}, "address_end": 0})
        t0 = time.perf_counter()
        slow = asyncio.create_task(xfer(tport, tcp(READ0), timeout=2))
        await asyncio.sleep(0.05)
        fast = await xfer(tport, tcp(READ10))
        assert fast is not None and fast[7] == 3 and time.perf_counter() - t0 < 0.4
        resp = await slow
        assert resp[7:] == bytes([3, 2, 0, 5]) and time.perf_counter() - t0 >= 0.55
    run(body)


def test_no_response_disconnect_and_ids():
    async def body(eng, tport, _rport):
        eng.faults.add({"action": "no_response", "remaining": 1})
        assert await xfer(tport, tcp(READ0), timeout=0.3) is None
        assert (await xfer(tport, tcp(READ0)))[7:] == bytes([3, 2, 0, 5])
        eng.faults.add({"action": "disconnect", "remaining": 1})
        assert await xfer(tport, tcp(READ0)) == b""
        eng.faults.add({"action": "wrong_transaction_id", "remaining": 1})
        resp = await xfer(tport, tcp(READ0, tid=0x1234))
        assert resp[:2] == b"\x12\x35" and resp[7:] == bytes([3, 2, 0, 5])
        eng.faults.add({"action": "wrong_unit_id", "params": {"unit": 9}, "remaining": 1})
        assert (await xfer(tport, tcp(READ0)))[6] == 9
        eng.faults.add({"action": "truncate", "params": {"bytes": 8}, "remaining": 1})
        assert len(await xfer(tport, tcp(READ0))) == 8
    run(body)


def test_rtu_over_tcp_crc_truncate_and_tcp_unaffected():
    async def body(eng, tport, rport):
        eng.faults.add({"action": "corrupt_crc", "remaining": 1})
        assert (await xfer(tport, tcp(READ0)))[7:] == bytes([3, 2, 0, 5])   # not applicable to MBAP
        raw = await xfer(rport, RtuFramer.build(1, READ0))
        good = RtuFramer.build(raw[0], raw[1:-2])
        assert raw[:-2] == good[:-2] == bytes([1, 3, 2, 0, 5])
        assert raw[-2:] == bytes(b ^ 0xFF for b in good[-2:])
        eng.faults.add({"action": "truncate", "params": {"bytes": 3}})
        assert await xfer(rport, RtuFramer.build(1, READ0)) == bytes([1, 3, 2])
    run(body)


def test_busy_window_then_disabled():
    async def body(eng, tport, _rport):
        eng.faults.add({"id": "b", "action": "busy", "params": {"seconds": 0.3}})
        assert (await xfer(tport, tcp(READ0)))[7:] == bytes([0x83, 6])
        assert (await xfer(tport, tcp(READ10)))[7:] == bytes([0x83, 6])
        await asyncio.sleep(0.35)
        assert (await xfer(tport, tcp(READ0)))[7:] == bytes([3, 2, 0, 5])
        rule = eng.faults.list()[0]
        assert rule["enabled"] is False and rule["triggers"] == 2
    run(body)


def test_freeze_stops_ticks_but_serves():
    async def body(eng, tport, _rport):
        pt = eng.devices["D"].by_name["ramp"]
        await asyncio.sleep(0.15)
        eng.faults.add({"id": "fz", "action": "freeze", "unit": 1})
        await asyncio.sleep(0.1)
        frozen = pt.value
        await asyncio.sleep(0.25)
        assert pt.value == frozen
        assert (await xfer(tport, tcp(READ10)))[7:9] == bytes([3, 4])
        assert eng.faults.list()[0]["active"] is True
        eng.faults.delete("fz")
        await asyncio.sleep(0.2)
        assert pt.value > frozen
    run(body)


def test_refuse_connections():
    async def body(eng, tport, rport):
        eng.faults.add({"id": "rf", "action": "refuse_connections", "listener": "tcp1"})
        assert await xfer(tport, tcp(READ0)) == b""
        assert (await xfer(rport, RtuFramer.build(1, READ0)))[:3] == bytes([1, 3, 2])   # other listener
        assert any(e.get("fault", {}) and e["fault"]["rule"] == "rf" for e in eng.trace(20))
        eng.faults.clear()
        assert (await xfer(tport, tcp(READ0)))[7:] == bytes([3, 2, 0, 5])
    run(body)


# ------------------------------------------------------------------ REST / WS / presets


@pytest.fixture()
def client():
    with TestClient(create_app(make_engine())) as c:
        yield c


def test_api_crud_and_ws(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        r = client.post("/api/faults", json={"action": "exception", "params": {"code": 2}, "unit": 1})
        assert r.status_code == 200
        rid = r.json()["id"]
        while True:
            msg = ws.receive_json()
            if msg["type"] == "faults":
                break
        assert msg["rules"][0]["id"] == rid and msg["rules"][0]["triggers"] == 0
    assert client.get("/api/faults").json()["rules"][0]["params"] == {"code": 2}
    r = client.put(f"/api/faults/{rid}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["unit"] == 1
    r = client.put(f"/api/faults/{rid}", json={"action": "delay", "params": {"ms": 5}})
    assert r.json()["action"] == "delay"
    assert client.put("/api/faults/zz", json={}).status_code == 404
    assert client.post("/api/faults", json={"action": "exception", "params": {"code": 7}}).status_code == 400
    assert client.post("/api/faults", content=b"[1]").status_code == 400
    assert client.delete(f"/api/faults/{rid}").status_code == 200
    assert client.delete(f"/api/faults/{rid}").status_code == 404
    assert client.get("/api/faults").json()["rules"] == []


def test_preset_round_trip(client):
    preset = """
- {id: slow, action: delay, params: {min_ms: 100, max_ms: 200}, unit: 1, probability: 0.5}
- {action: exception, params: {code: "0B"}, function_codes: [16], address_start: 100, address_end: 199}
- {action: freeze, unit: 1, enabled: false}
"""
    r = client.post("/api/faults/preset", content=preset.encode())
    assert r.status_code == 200 and len(r.json()["rules"]) == 3
    exp = client.get("/api/faults/export")
    assert exp.headers["content-disposition"].endswith('filename="faults.json"')
    data = exp.json()
    assert data[1]["params"] == {"code": 11} and data[0]["id"] == "slow"
    assert client.post("/api/faults/clear").status_code == 200
    assert client.get("/api/faults").json()["rules"] == []
    assert client.post("/api/faults/preset", content=json.dumps(data).encode()).status_code == 200
    assert client.get("/api/faults/export").json() == data
    # listed rules (with runtime fields) can be posted back as a preset, append mode keeps the rest
    listed = client.get("/api/faults").json()["rules"]
    r = client.post("/api/faults/preset?replace=false", content=json.dumps([{**listed[2], "id": "x"}]).encode())
    assert len(r.json()["rules"]) == 4
    bad = client.post("/api/faults/preset", content=b"- {action: exception, params: {code: 9}}")
    assert bad.status_code == 400 and "rule #1" in bad.json()["detail"]
    assert len(client.get("/api/faults").json()["rules"]) == 4   # all-or-nothing

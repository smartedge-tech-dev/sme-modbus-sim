import math

import pytest

from sme_modbus_sim.config import SimConfig
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.protocol import ModbusError, datatypes as dt


def _engine(points, **dev):
    d = {"name": "D", "unit_id": 1, "points": points, **dev}
    return Engine(SimConfig.model_validate({"devices": [d]}))


TYPES = [
    ("u16", "uint16", 1, 12345), ("i16", "int16", 1, -1234), ("u32", "uint32", 2, 3_000_000_000),
    ("i32", "int32", 2, -70000), ("f32", "float32", 2, 1.5), ("u64", "uint64", 4, 2**60 + 7),
    ("i64", "int64", 4, -(2**40)), ("f64", "float64", 4, -2.25),
]


@pytest.mark.parametrize("bo,wo", [("big", "big"), ("big", "little"), ("little", "big"), ("little", "little")])
def test_point_register_mapping_all_datatypes(bo, wo):
    pts, addr = [], 0
    for name, typ, n, v in TYPES:
        pts.append({"name": name, "table": "holding", "address": addr, "datatype": typ, "value": v})
        addr += n
    pts += [{"name": "s", "table": "holding", "address": addr, "datatype": "string", "count": 3, "value": "AB1"},
            {"name": "e", "table": "holding", "address": addr + 3, "datatype": "enum", "labels": {0: "A", 7: "B"},
             "value": "B"},
            {"name": "c", "table": "coil", "address": 0, "value": True},
            {"name": "di", "table": "discrete", "address": 5, "value": 1}]
    e = _engine(pts, byte_order=bo, word_order=wo)
    dev = e.devices["D"]
    for name, typ, n, v in TYPES:
        p = dev.by_name[name]
        assert p.raw() == dt.encode(v, typ, bo, wo), name
        assert p.read() == v
    assert dev.by_name["s"].read() == "AB1" and dev.by_name["e"].read() == 7
    assert dev.store.get("coil", 0) == [1] and dev.store.get("discrete", 5) == [1]


def test_scale_offset_and_point_order_override():
    e = _engine([{"name": "a", "table": "input", "address": 0, "datatype": "int32", "scale": 0.1,
                  "offset": -10, "value": 5.0, "word_order": "big"}], word_order="little")
    p = e.devices["D"].by_name["a"]
    assert p.raw() == dt.encode(150, "int32", "big", "big") and p.read() == pytest.approx(5.0)


def test_bit_points_share_a_register():
    e = _engine([{"name": "b0", "table": "holding", "address": 3, "datatype": "bit", "bit": 0, "value": 1},
                 {"name": "b5", "table": "holding", "address": 3, "datatype": "bit", "bit": 5, "value": 1},
                 {"name": "ro", "table": "holding", "address": 3, "datatype": "bit", "bit": 9, "access": "r"}])
    dev = e.devices["D"]
    assert dev.store.get("holding", 3) == [0b100001]
    e.set_point("D", "b0", False)
    assert dev.store.get("holding", 3) == [0b100000] and dev.by_name["b5"].read() is True
    dev.store.write("holding", 3, [0b100001])  # protocol write leaving the read-only bit alone
    assert dev.by_name["b0"].value is True
    with pytest.raises(ModbusError) as ei:
        dev.store.write("holding", 3, [0b1000100001])
    assert ei.value.exception_code == 2


def test_behaviors_tick_clamp_and_cross_device_pv():
    cfg = SimConfig.model_validate({"devices": [
        {"name": "A", "unit_id": 1, "points": [
            {"name": "sp", "table": "holding", "address": 0, "datatype": "int16", "value": 100},
            {"name": "out", "table": "input", "address": 0, "datatype": "int16", "min": -50, "max": 50,
             "behavior": {"type": "expr", "expr": "sp"}},
            {"name": "r", "table": "input", "address": 1, "datatype": "int16",
             "behavior": {"type": "ramp", "target": "sp", "rate": 10}},
            {"name": "sat", "table": "input", "address": 2, "datatype": "uint16",
             "behavior": {"type": "expr", "expr": "70000"}},
            {"name": "cnt", "table": "input", "address": 3, "datatype": "uint16", "value": 65530,
             "behavior": {"type": "counter", "rate": "5"}},
            {"name": "bad", "table": "input", "address": 4, "behavior": {"type": "expr", "expr": "nope + 1"}}]},
        {"name": "B", "unit_id": 2, "points": [
            {"name": "mirror", "table": "input", "address": 0, "datatype": "int16",
             "behavior": {"type": "expr", "expr": "pv('A', 'r') * 2"}}]}]})
    e = Engine(cfg)
    e.step(1.0)
    a = e.devices["A"].by_name
    assert a["out"].read() == 50  # clamped to max
    assert a["r"].read() == 10  # ramped 10/s
    assert a["sat"].read() == 65535  # non-counter saturates
    assert a["cnt"].read() == 65535
    assert e.devices["B"].by_name["mirror"].read() == 20
    assert "NameError" in a["bad"].error and a["bad"].read() == 0
    e.step(1.0)
    assert a["r"].read() == 20 and a["cnt"].read() == 4  # counter wraps 65535 + 5 -> 4
    assert e.sim_time == 2.0 and any(ev["level"] == "error" for ev in e.events)


def test_write_validators():
    e = _engine([{"name": "f", "table": "holding", "address": 0, "datatype": "float32"},
                 {"name": "sp", "table": "holding", "address": 2, "datatype": "int16", "scale": 0.1,
                  "min": -50, "max": 50, "behavior": {"type": "counter", "rate": "1"}},
                 {"name": "ro", "table": "holding", "address": 3, "access": "r", "value": 9},
                 {"name": "coil", "table": "coil", "address": 0}])
    st = e.devices["D"].store

    def code(table, addr, values):
        with pytest.raises(ModbusError) as ei:
            st.write(table, addr, values)
        return ei.value.exception_code

    assert code("holding", 1, [1, 2]) == 2  # partial write of float32 (+ next register)
    assert code("holding", 0, [0x4000]) == 2  # first half only
    assert code("holding", 3, [1]) == 2  # access r
    assert code("holding", 2, [501]) == 3  # 50.1 > max
    assert code("holding", 9, [1]) == 2  # undefined
    st.write("holding", 0, dt.encode(2.5, "float32") + [dt.encode(-20.0, "int16", scale=0.1)[0]])
    p = e.devices["D"].by_name
    assert p["f"].value == 2.5 and p["sp"].value == pytest.approx(-20.0)
    st.write("coil", 0, [1])
    assert p["coil"].value is True
    # protocol write overrides a behavior's current value; the behavior continues from it
    e.step(1.0)
    assert p["sp"].read() == pytest.approx(-19.0)
    with pytest.raises(ValueError):
        e.set_point("D", "sp", 60)
    assert e.set_point("D", "ro", 3) == 3  # engine-side write bypasses access
    with pytest.raises(KeyError):
        e.set_point("D", "missing", 1)


def test_snapshot_history_and_running():
    e = _engine([{"name": "x", "table": "input", "address": 0, "behavior": {"type": "expr", "expr": "t"}}])
    q = e.subscribe()
    e.step(1.0)
    e._scan_changes()
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    vals = [m for m in msgs if m["type"] == "values"]
    assert vals and vals[0]["changes"][0]["k"] == "x" and vals[0]["changes"][0]["value"] == 1
    assert e.history("D", "x")[-1][1] == 1.0
    snap = e.snapshot()
    assert snap["devices"][0]["points"][0]["registers"] == 1 and snap["listeners"][0]["id"] == "tcp1"
    e.set_running(False, 5)
    assert e.config.simulation.running is False and e.config.simulation.time_scale == 5
    with pytest.raises(ValueError):
        e.set_running(None, 0)
    assert not math.isnan(snap["sim_hour"])


def test_bad_behavior_rejected_at_build():
    with pytest.raises(ValueError, match="D/x"):
        _engine([{"name": "x", "table": "input", "address": 0, "behavior": {"type": "expr", "expr": "import os"}}])


def test_identification_defaults_and_overrides():
    from sme_modbus_sim import __version__
    e = _engine([{"name": "a", "table": "holding", "address": 0}])
    assert e.devices["D"].store.identification == {0: "SmartEdge", 1: "SME-MODBUS-SIM", 2: __version__}
    e = _engine([{"name": "a", "table": "holding", "address": 0}],
                identification={"vendor_name": "ACME", "model_name": "X1", "user_application_name": "site"})
    ident = e.devices["D"].store.identification
    assert (ident[0], ident[1], ident[5], ident[6]) == ("ACME", "SME-MODBUS-SIM", "X1", "site")


def test_fc16_fc17_go_through_engine_validators():
    import struct

    from sme_modbus_sim.protocol.pdu import handle_pdu
    e = _engine([{"name": "sp", "table": "holding", "address": 0, "value": 5, "min": 0, "max": 100},
                 {"name": "ro", "table": "holding", "address": 1, "access": "r", "value": 1},
                 {"name": "big", "table": "holding", "address": 2, "datatype": "uint32", "value": 7}])
    store = e.devices["D"].store

    def mask(addr, and_m, or_m):
        return handle_pdu(store, bytes([0x16]) + struct.pack(">HHH", addr, and_m, or_m))

    def rw(waddr, values):
        return handle_pdu(store, bytes([0x17]) + struct.pack(">HHHHB", 0, 4, waddr, len(values), 2 * len(values))
                          + struct.pack(f">{len(values)}H", *values))

    assert mask(0, 0x0000, 0x00FF) == bytes([0x96, 0x03])        # 255 > max
    assert mask(1, 0x0000, 0x0002) == bytes([0x96, 0x02])        # read-only point
    assert mask(2, 0xFFFF, 0x0000) == bytes([0x96, 0x02])        # partial write of uint32
    assert mask(0, 0x00F0, 0x0003)[0] == 0x16 and e.devices["D"].by_name["sp"].value == 3
    assert rw(3, [1]) == bytes([0x97, 0x02])                     # partial uint32
    assert rw(1, [9]) == bytes([0x97, 0x02])                     # read-only
    assert rw(0, [101]) == bytes([0x97, 0x03])                   # > max
    assert rw(2, [0, 9]) == bytes([0x17, 8, 0, 3, 0, 1, 0, 0, 0, 9])
    assert e.devices["D"].by_name["big"].value == 9

"""Config model validation (design §2.4, §5.1)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from sme_modbus_sim.config import PointConfig, SimConfig, dump_yaml, load_file, parse_text, register_count

DEMO = Path(__file__).resolve().parents[1] / "configs" / "demo-site.yaml"


def _cfg(devices: str, listeners: str = "") -> SimConfig:
    return parse_text((listeners or "") + "\ndevices:\n" + devices)


def test_defaults():
    cfg = parse_text("")
    assert cfg.simulation.tick_seconds == 1.0 and cfg.devices == []
    (lst,) = cfg.listeners
    assert (lst.id, lst.type, lst.host, lst.port, lst.unit_id_mode, lst.unknown_unit) == (
        "tcp1", "tcp", "0.0.0.0", 5020, "strict", "ignore")


def test_point_defaults_by_table():
    c = PointConfig(name="c", table="coil")
    d = PointConfig(name="d", table="discrete")
    h = PointConfig(name="h", table="holding")
    i = PointConfig(name="i", table="input")
    assert (c.datatype, c.access, d.access) == ("bool", "rw", "r")
    assert (h.datatype, h.access, i.datatype, i.access) == ("uint16", "rw", "uint16", "r")


@pytest.mark.parametrize("dt, extra, n", [
    ("int16", {}, 1), ("enum", {}, 1), ("bit", {"bit": 3}, 1), ("float32", {}, 2), ("uint32", {}, 2),
    ("int64", {}, 4), ("float64", {}, 4), ("string", {"count": 5}, 5)])
def test_register_count(dt, extra, n):
    assert register_count(PointConfig(name="p", table="holding", datatype=dt, **extra)) == n


def test_rtu_serial_listener_defaults():
    cfg = parse_text("listeners: [{id: com1, type: rtu, port: COM5}, {id: t, port: 502}]")
    lst = cfg.listeners[0]
    assert (lst.port, lst.baudrate, lst.bytesize, lst.parity, lst.stopbits, lst.strict_timing) == (
        "COM5", 19200, 8, "E", 1, False)
    no_parity = parse_text("listeners: [{id: c, type: rtu, port: /dev/ttyUSB0, parity: N, baudrate: 19200}]")
    assert (no_parity.listeners[0].parity, no_parity.listeners[0].stopbits) == ("N", 2)  # spec: N -> 2 stop bits
    compat = parse_text("listeners: [{id: c, type: rtu, port: COM3, parity: N, stopbits: 1}]")
    assert compat.listeners[0].stopbits == 1  # non-conforming 8N1 accepted
    assert "baudrate" not in dump_yaml(cfg.listeners[1])


@pytest.mark.parametrize("ls, msg", [
    ("[{id: c, type: rtu}]", "needs port"),
    ("[{id: c, type: rtu, port: COM5, parity: X}]", "parity"),
    ("[{id: c, type: rtu, port: COM5, stopbits: 3}]", "stopbits"),
    ("[{id: c, type: rtu, port: COM5, bytesize: 7}]", "bytesize"),
    ("[{id: t, port: 502, baudrate: 9600}]", "only apply to type rtu"),
    ("[{id: t, port: COM5}]", "TCP port"),
    ("[{id: t, port: 70000}]", "TCP port"),
    ("[{id: a, type: rtu, port: COM5}, {id: b, type: rtu, port: COM5}]", "same port"),
])
def test_rtu_serial_listener_invalid(ls, msg):
    with pytest.raises(ValidationError, match=msg):
        parse_text(f"listeners: {ls}")


@pytest.mark.parametrize("point, msg", [
    ("{name: x, table: coil, datatype: int16}", "datatype must be bool"),
    ("{name: x, table: holding, datatype: bool}", "use datatype bit"),
    ("{name: x, table: input, access: rw}", "read-only"),
    ("{name: x, table: discrete, access: rw}", "read-only"),
    ("{name: x, table: holding, datatype: bit}", "needs bit"),
    ("{name: x, table: holding, bit: 2}", "only valid with datatype bit"),
    ("{name: x, table: holding, datatype: string}", "needs count"),
    ("{name: x, table: holding, labels: {0: a}}", "only valid with datatype enum"),
    ("{name: x, table: holding, datatype: enum, labels: {0: OFF, 1: ON}}", "quote it"),
    ("{name: x, table: holding, scale: 0}", "scale must not be 0"),
    ("{name: x, table: holding, address: 65535, datatype: float32}", "exceeds address"),
    ("{name: x, table: holding, behavior: {expr: a}}", "needs a 'type'"),
    ("{name: x}", "need a table"),
])
def test_point_errors(point, msg):
    with pytest.raises(ValidationError, match=msg):
        _cfg(f"  - {{name: D, unit_id: 1, points: [{point}]}}")


def test_device_errors():
    with pytest.raises(ValidationError, match="duplicate device name"):
        _cfg("  - {name: D, unit_id: 1}\n  - {name: D, unit_id: 2}")
    with pytest.raises(ValidationError, match="unit_id 1 used by both 'A' and 'B'"):
        _cfg("  - {name: A, unit_id: 1}\n  - {name: B, unit_id: 1}")
    with pytest.raises(ValidationError, match="unknown listener 'nope'"):
        _cfg("  - {name: A, unit_id: 1, listeners: [nope]}")
    with pytest.raises(ValidationError, match="duplicate point names"):
        _cfg("  - {name: A, unit_id: 1, points: [{name: p, table: coil}, {name: p, table: coil}]}")
    with pytest.raises(ValidationError):
        _cfg("  - {name: A, unit_id: 248}")
    with pytest.raises(ValidationError, match="unknown template"):
        _cfg("  - {name: A, unit_id: 1, template: nope}")


def test_same_unit_on_different_listeners_ok():
    ls = "listeners: [{id: a, port: 5020}, {id: b, port: 5021, type: rtu_over_tcp}]"
    cfg = _cfg("  - {name: A, unit_id: 1, listeners: [a]}\n  - {name: B, unit_id: 1, listeners: [b]}", ls)
    assert cfg.device_listeners(cfg.devices[0]) == ["a"]
    with pytest.raises(ValidationError, match="unit_id 1 used by both"):
        _cfg("  - {name: A, unit_id: 1}\n  - {name: B, unit_id: 1, listeners: [b]}", ls)


def test_listener_errors():
    with pytest.raises(ValidationError, match="duplicate listener id"):
        parse_text("listeners: [{id: a, port: 1}, {id: a, port: 2}]")
    with pytest.raises(ValidationError, match="same host:port"):
        parse_text("listeners: [{id: a}, {id: b}]")
    with pytest.raises(ValidationError, match="default_unit 9 is not a device"):
        _cfg("  - {name: A, unit_id: 1}", "listeners: [{id: a, unit_id_mode: any, default_unit: 9}]")
    cfg = _cfg("  - {name: A, unit_id: 1}", "listeners: [{id: a, unit_id_mode: any, default_unit: 1}]")
    assert cfg.listeners[0].default_unit == 1


def test_overlap_rules():
    with pytest.raises(ValidationError, match=r"holding 1\.\.1 overlaps 'a' \(0\.\.1\)"):
        _cfg("  - {name: D, unit_id: 1, points: [{name: a, table: holding, address: 0, datatype: float32},"
             " {name: b, table: holding, address: 1}]}")
    with pytest.raises(ValidationError, match="bit 2 already used by 'a'"):
        _cfg("  - {name: D, unit_id: 1, points: [{name: a, table: input, address: 5, datatype: bit, bit: 2},"
             " {name: b, table: input, address: 5, datatype: bit, bit: 2}]}")
    with pytest.raises(ValidationError, match="input 5 overlaps 'w'"):
        _cfg("  - {name: D, unit_id: 1, points: [{name: w, table: input, address: 4, datatype: uint32},"
             " {name: b, table: input, address: 5, datatype: bit, bit: 0}]}")
    with pytest.raises(ValidationError, match="overlaps"):
        _cfg("  - {name: D, unit_id: 1, points: [{name: s, table: holding, address: 0, datatype: string, count: 4},"
             " {name: b, table: holding, address: 3}]}")
    # distinct bits share a register; the same address in different tables is fine
    cfg = _cfg("  - {name: D, unit_id: 1, points: ["
               "{name: a, table: input, address: 5, datatype: bit, bit: 0},"
               " {name: b, table: input, address: 5, datatype: bit, bit: 1},"
               " {name: c, table: holding, address: 5}, {name: d, table: coil, address: 5},"
               " {name: e, table: discrete, address: 5}]}")
    assert len(cfg.devices[0].points) == 5


def test_demo_site_loads_and_round_trips():
    cfg = load_file(DEMO)
    assert [lst.type for lst in cfg.listeners] == ["tcp", "rtu_over_tcp"]
    assert [(d.name, d.unit_id) for d in cfg.devices] == [("PCS-1", 1), ("BMS-1", 2), ("Meter-1", 3), ("Env-1", 4)]
    assert cfg.device_listeners(cfg.devices[3]) == ["gw1"]
    mode = cfg.devices[3].points[1]
    assert mode.labels == {0: "OFF", 1: "ON", 2: "AUTO"}
    again = parse_text(dump_yaml(cfg))
    assert again.model_dump() == cfg.model_dump()

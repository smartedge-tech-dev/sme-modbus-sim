"""Templates: expansion, overrides, address allocation, expression sanity."""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from sme_modbus_sim.behaviors import FUNCTIONS, compile_expr, ident
from sme_modbus_sim.config import DeviceConfig, load_file, parse_text
from sme_modbus_sim.templates import TEMPLATES, expand_device, template_catalog

DEMO = Path(__file__).resolve().parents[1] / "configs" / "demo-site.yaml"
ENGINE_NAMES = {"t", "dt", "hour", "pv"}  # supplied by the engine (as in the sister engine's base names)


def _dev(**kw) -> DeviceConfig:
    return DeviceConfig.model_validate({"name": "D", "unit_id": 1, **kw})


def _exprs(p):
    b = p.behavior or {}
    return [b[k] for k in ("expr", "target", "rate") if k in b]


def _check_exprs(points):
    known = {ident(p.name) for p in points} | set(FUNCTIONS) | ENGINE_NAMES
    for p in points:
        for src in _exprs(p):
            compile_expr(src)
            names = {n.id for n in ast.walk(ast.parse(str(src), mode="eval")) if isinstance(n, ast.Name)}
            assert not names - known, f"{p.name}: unknown names {names - known}"


def test_catalog():
    names = [t["name"] for t in template_catalog()]
    assert names == ["battery_pcs", "bms", "power_meter", "env_sensor", "custom"]


@pytest.mark.parametrize("name", list(TEMPLATES))
@pytest.mark.parametrize("linked", [False, True])
def test_every_template_expands(name, linked):
    params = {"bms_device": "B", "pcs_device": "P"} if linked else {}
    params = {k: v for k, v in params.items() if k in TEMPLATES[name]["params"]}
    pts = expand_device(_dev(template=name, params=params))
    assert all(p.table and p.address is not None and p.datatype for p in pts)
    _check_exprs(pts)
    if params:
        assert any("pv(" in str(p.behavior) for p in pts)


def test_params_merge_and_cross_refs():
    pts = {p.name: p for p in expand_device(_dev(template="battery_pcs",
                                                 params={"rated_kw": 100, "bms_device": "BMS-1"}))}
    assert pts["p_setpoint"].max == 100 and pts["p_setpoint"].min == -100
    assert "pv('BMS-1', 'soc')" in pts["p_actual"].behavior["target"]
    assert pts["dc_voltage"].behavior["expr"] == "pv('BMS-1', 'pack_voltage')"
    alarms = [p for p in pts.values() if p.datatype == "bit"]
    assert {p.address for p in alarms} == {8} and len({p.bit for p in alarms}) == len(alarms)


def test_override_add_and_remove():
    pts = {p.name: p for p in expand_device(_dev(
        template="power_meter",
        points=[{"name": "frequency", "value": 60, "behavior": {"type": "static"}},
                {"name": "extra", "table": "holding", "datatype": "uint32"}],
        removed=["export_energy_f64"]))}
    assert pts["frequency"].value == 60 and pts["frequency"].datatype == "float32"
    assert pts["frequency"].address == 18 and pts["frequency"].behavior == {"type": "static"}
    assert "export_energy_f64" not in pts
    assert (pts["extra"].table, pts["extra"].address) == ("holding", 8)  # after serial_number (0..7)


def test_override_errors():
    with pytest.raises(ValidationError, match="read-only"):
        parse_text("devices: [{name: D, unit_id: 1, template: bms, points: [{name: soc, access: rw}]}]")
    with pytest.raises(ValidationError, match="needs a table"):
        parse_text("devices: [{name: D, unit_id: 1, template: bms, points: [{name: nope, value: 1}]}]")
    with pytest.raises(ValidationError, match="overlaps"):
        parse_text("devices: [{name: D, unit_id: 1, template: bms,"
                   " points: [{name: x, table: input, address: 3}]}]")


def test_address_auto_allocation():
    pts = expand_device(_dev(points=[
        {"name": "a", "table": "holding", "datatype": "float32"},
        {"name": "b", "table": "holding", "address": 10, "datatype": "uint32"},
        {"name": "c", "table": "holding", "datatype": "string", "count": 3},
        {"name": "d", "table": "holding"},
        {"name": "e", "table": "coil"},
        {"name": "f", "table": "coil"},
        {"name": "g", "table": "input", "datatype": "bit", "bit": 4}]))
    addr = {p.name: p.address for p in pts}
    # holding: highest explicit range ends at 12 -> a 12..13, c 14..16, d 17
    assert addr == {"a": 12, "b": 10, "c": 14, "d": 17, "e": 0, "f": 1, "g": 0}
    env = {p.name: p.address for p in expand_device(_dev(template="env_sensor"))}
    assert env == {"temperature": 0, "humidity": 1}


def test_demo_site_expands_and_cross_refs_resolve():
    cfg = load_file(DEMO)
    exp = {d.name: {p.name: p for p in expand_device(d)} for d in cfg.devices}
    assert exp["Meter-1"]["serial_number"].value == "MTR00001"
    env = exp["Env-1"]
    assert (env["fan_status"].table, env["fan_status"].address) == ("discrete", 0)
    assert (env["mode"].address, env["mode"].access) == (0, "rw")
    refs = 0
    for name, pts in exp.items():
        _check_exprs(list(pts.values()))
        for p in pts.values():  # every pv() points at an existing device / point
            for src in _exprs(p):
                for node in ast.walk(ast.parse(str(src), mode="eval")):
                    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "pv":
                        dev, pt = (a.value for a in node.args)
                        assert dev in exp and pt in exp[dev], f"{name}/{p.name}: pv({dev}, {pt})"
                        refs += 1
    assert refs >= 4

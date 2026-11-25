"""Built-in device templates.

Mechanism (``@template``, params merge, override by name, ``removed``, ``template_catalog``) follows
sme-bac-sim ``sme_bacnet_sim/templates.py`` @ ebd27ec; instance auto-assignment became address auto-allocation.

The register maps below are ORIGINAL, vendor-neutral layouts invented for this simulator. They do not
reproduce any manufacturer's communication specification (design §5.3, §12 Q6/Q12).

A template is a function ``params -> list[point dict]``. Sign convention: active power > 0 = discharge /
export from the battery, < 0 = charge. Cross-device references use ``pv("Device", "point")`` and are
enabled through params such as ``bms_device`` / ``pcs_device`` (empty = standalone).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .config import DeviceConfig, PointConfig, check_layout, register_count

Pt = Dict[str, Any]
TEMPLATES: Dict[str, Dict[str, Any]] = {}


def template(name: str, title: str, params: Dict[str, Any]):
    def deco(fn: Callable[[Dict[str, Any]], List[Pt]]):
        TEMPLATES[name] = {"name": name, "title": title, "params": params, "build": fn}
        return fn

    return deco


def _ref(device: Any, point: str, fallback: str) -> str:
    """Expression for another device's point, or ``fallback`` when no device is configured."""
    device = str(device or "").strip()
    return f"pv({device!r}, {point!r})" if device else fallback


def _num(p: Dict[str, Any], key: str) -> float:
    return float(p[key])


@template("battery_pcs", "Battery PCS (generic)",
          {"rated_kw": 50, "rated_kvar": 50, "ac_voltage_v": 400, "frequency_hz": 50,
           "dc_voltage_v": 716.8, "bms_device": "", "ramp_kw_per_s": 10})
def _battery_pcs(p):
    kw, kvar, rate = _num(p, "rated_kw"), _num(p, "rated_kvar"), _num(p, "ramp_kw_per_s")
    vac, hz = _num(p, "ac_voltage_v"), _num(p, "frequency_hz")
    soc = _ref(p.get("bms_device"), "soc", "50")
    dc_v = _ref(p.get("bms_device"), "pack_voltage", str(_num(p, "dc_voltage_v")))
    # discharge blocked at SOC <= 5 %, charge blocked at SOC >= 95 %
    p_target = (f"clamp(p_setpoint, -{kw}, {kw}) * (state == 2)"
                f" * (0 if (p_setpoint > 0 and {soc} <= 5) or (p_setpoint < 0 and {soc} >= 95) else 1)")
    return [
        # --- control (coil / holding)
        {"name": "run_command", "table": "coil", "address": 0, "value": False,
         "description": "Run (1) / stop (0) command; stop also clears a latched fault"},
        {"name": "p_setpoint", "table": "holding", "address": 0, "datatype": "int16", "scale": 0.1,
         "units": "kW", "value": 0, "min": -kw, "max": kw,
         "description": "Active power setpoint (+ discharge / - charge)"},
        {"name": "q_setpoint", "table": "holding", "address": 1, "datatype": "int16", "scale": 0.1,
         "units": "kvar", "value": 0, "min": -kvar, "max": kvar, "description": "Reactive power setpoint"},
        # --- status / measurements (input)
        {"name": "state", "table": "input", "address": 0, "datatype": "enum", "value": 0,
         "labels": {0: "Stopped", 2: "Running", 3: "Fault"},
         "description": "3 (Fault) is latched until run_command is cleared",
         "behavior": {"type": "expr", "expr": "0 if not run_command else (3 if state == 3 else 2)"}},
        {"name": "p_actual", "table": "input", "address": 1, "datatype": "int16", "scale": 0.1, "units": "kW",
         "value": 0, "behavior": {"type": "ramp", "target": p_target, "rate": rate}},
        {"name": "q_actual", "table": "input", "address": 2, "datatype": "int16", "scale": 0.1,
         "units": "kvar", "value": 0,
         "behavior": {"type": "ramp", "target": f"clamp(q_setpoint, -{kvar}, {kvar}) * (state == 2)",
                      "rate": rate}},
        {"name": "ac_voltage", "table": "input", "address": 3, "datatype": "uint16", "scale": 0.1, "units": "V",
         "value": vac, "behavior": {"type": "random_walk", "min": vac * 0.98, "max": vac * 1.02,
                                    "step": vac * 0.001}},
        {"name": "ac_current", "table": "input", "address": 4, "datatype": "uint16", "scale": 0.1, "units": "A",
         "value": 0, "behavior": {"type": "expr",
                                  "expr": "sqrt(p_actual ** 2 + q_actual ** 2) * 1000 / (sqrt(3) * ac_voltage)"}},
        {"name": "frequency", "table": "input", "address": 5, "datatype": "uint16", "scale": 0.01, "units": "Hz",
         "value": hz, "behavior": {"type": "random_walk", "min": hz - 0.05, "max": hz + 0.05, "step": 0.005}},
        {"name": "dc_voltage", "table": "input", "address": 6, "datatype": "uint16", "scale": 0.1, "units": "V",
         "value": _num(p, "dc_voltage_v"), "behavior": {"type": "expr", "expr": dc_v}},
        {"name": "dc_current", "table": "input", "address": 7, "datatype": "int16", "scale": 0.1, "units": "A",
         "value": 0, "behavior": {"type": "expr", "expr": "p_actual * 1000 / max(dc_voltage, 1)"}},
        # alarm word: input 8, one bit each
        {"name": "alarm_fault", "table": "input", "address": 8, "datatype": "bit", "bit": 0, "value": 0,
         "behavior": {"type": "expr", "expr": "int(state == 3)"}},
        {"name": "alarm_soc_limit", "table": "input", "address": 8, "datatype": "bit", "bit": 1, "value": 0,
         "description": "Setpoint limited by SOC",
         "behavior": {"type": "expr", "expr": f"int(state == 2 and p_setpoint != 0 and ({p_target}) == 0)"}},
        {"name": "alarm_grid", "table": "input", "address": 8, "datatype": "bit", "bit": 2, "value": 0,
         "description": "AC voltage / frequency out of range",
         "behavior": {"type": "expr",
                      "expr": f"int(abs(ac_voltage - {vac}) > {vac * 0.1} or abs(frequency - {hz}) > 0.5)"}},
        {"name": "rated_power", "table": "input", "address": 9, "datatype": "uint16", "scale": 0.1,
         "units": "kW", "value": kw},
        # energy (uint32, 0.1 kWh)
        {"name": "discharged_energy", "table": "input", "address": 10, "datatype": "uint32", "scale": 0.1,
         "units": "kWh", "value": 0, "behavior": {"type": "counter", "rate": "max(p_actual, 0) / 3600"}},
        {"name": "charged_energy", "table": "input", "address": 12, "datatype": "uint32", "scale": 0.1,
         "units": "kWh", "value": 0, "behavior": {"type": "counter", "rate": "max(-p_actual, 0) / 3600"}},
    ]


@template("bms", "Battery management system (generic)",
          {"capacity_kwh": 100, "max_power_kw": 50, "cells_series": 224, "initial_soc": 50, "pcs_device": ""})
def _bms(p):
    cap, pmax, cells = _num(p, "capacity_kwh"), _num(p, "max_power_kw"), int(p["cells_series"])
    power = _ref(p.get("pcs_device"), "p_actual", "0")
    soc_rate = (f"0 if (soc >= 100 and ({power}) < 0) or (soc <= 0 and ({power}) > 0)"
                f" else -({power}) / {cap} * 100 / 3600")
    return [
        {"name": "soc", "table": "input", "address": 0, "datatype": "uint16", "scale": 0.1, "units": "%",
         "value": _num(p, "initial_soc"), "min": 0, "max": 100,
         "behavior": {"type": "counter", "rate": soc_rate}},
        {"name": "soh", "table": "input", "address": 1, "datatype": "uint16", "scale": 0.1, "units": "%",
         "value": 98.5},
        {"name": "pack_voltage", "table": "input", "address": 2, "datatype": "uint16", "scale": 0.1, "units": "V",
         "value": cells * 3.3,
         "behavior": {"type": "expr", "expr": f"{cells} * (3.1 + 0.25 * soc / 100) - pack_current * 0.05"}},
        {"name": "pack_current", "table": "input", "address": 3, "datatype": "int16", "scale": 0.1, "units": "A",
         "value": 0, "description": "+ discharge / - charge",
         "behavior": {"type": "expr", "expr": f"({power}) * 1000 / max(pack_voltage, 1)"}},
        {"name": "cell_voltage_max", "table": "input", "address": 4, "datatype": "uint16", "scale": 0.001,
         "units": "V", "value": 3.3,
         "behavior": {"type": "expr", "expr": f"pack_voltage / {cells} + 0.012 + noise(0.002)"}},
        {"name": "cell_voltage_min", "table": "input", "address": 5, "datatype": "uint16", "scale": 0.001,
         "units": "V", "value": 3.3,
         "behavior": {"type": "expr", "expr": f"pack_voltage / {cells} - 0.012 + noise(0.002)"}},
        {"name": "temperature_max", "table": "input", "address": 6, "datatype": "int16", "scale": 0.1,
         "units": "degC", "value": 25,
         "behavior": {"type": "follow", "target": "27 + abs(pack_current) * 0.05", "tau": 600}},
        {"name": "temperature_min", "table": "input", "address": 7, "datatype": "int16", "scale": 0.1,
         "units": "degC", "value": 24,
         "behavior": {"type": "follow", "target": "24 + abs(pack_current) * 0.02", "tau": 600}},
        {"name": "max_charge_power", "table": "input", "address": 8, "datatype": "uint16", "scale": 0.1,
         "units": "kW", "value": pmax,
         "behavior": {"type": "expr", "expr": f"0 if soc >= 100 else ({pmax} * 0.5 if soc > 90 else {pmax})"}},
        {"name": "max_discharge_power", "table": "input", "address": 9, "datatype": "uint16", "scale": 0.1,
         "units": "kW", "value": pmax,
         "behavior": {"type": "expr", "expr": f"0 if soc <= 0 else ({pmax} * 0.5 if soc < 10 else {pmax})"}},
        # alarm word: input 10
        {"name": "alarm_soc_low", "table": "input", "address": 10, "datatype": "bit", "bit": 0, "value": 0,
         "behavior": {"type": "expr", "expr": "int(soc < 10)"}},
        {"name": "alarm_soc_high", "table": "input", "address": 10, "datatype": "bit", "bit": 1, "value": 0,
         "behavior": {"type": "expr", "expr": "int(soc > 95)"}},
        {"name": "alarm_over_temperature", "table": "input", "address": 10, "datatype": "bit", "bit": 2,
         "value": 0, "behavior": {"type": "expr", "expr": "int(temperature_max > 45)"}},
        {"name": "alarm_cell_imbalance", "table": "input", "address": 10, "datatype": "bit", "bit": 3,
         "value": 0, "behavior": {"type": "expr", "expr": "int(cell_voltage_max - cell_voltage_min > 0.05)"}},
        {"name": "capacity", "table": "input", "address": 11, "datatype": "uint16", "scale": 0.1,
         "units": "kWh", "value": cap},
    ]


@template("power_meter", "Three-phase power meter (generic)",
          {"voltage_v": 230, "frequency_hz": 50, "base_load_kw": 30, "pcs_device": ""})
def _power_meter(p):
    v, hz, load = _num(p, "voltage_v"), _num(p, "frequency_hz"), _num(p, "base_load_kw")
    pcs = _ref(p.get("pcs_device"), "p_actual", "0")
    # site flow at the grid point: load (daily profile) - battery output; > 0 = import
    flow = f"{load} * (1 + 0.3 * sin(2 * pi * (hour - 8) / 24)) + noise({load * 0.02}) - ({pcs})"
    pts: List[Pt] = []
    for i in (1, 2, 3):
        pts.append({"name": f"voltage_l{i}", "table": "input", "address": 2 * (i - 1), "datatype": "float32",
                    "units": "V", "value": v,
                    "behavior": {"type": "random_walk", "min": v * 0.97, "max": v * 1.03, "step": v * 0.001}})
    for i in (1, 2, 3):
        pts.append({"name": f"current_l{i}", "table": "input", "address": 6 + 2 * (i - 1),
                    "datatype": "float32", "units": "A", "value": 0,
                    "behavior": {"type": "expr",
                                 "expr": f"sqrt(active_power ** 2 + reactive_power ** 2) * 1000 / (3 * voltage_l{i})"}})
    pts += [
        {"name": "active_power", "table": "input", "address": 12, "datatype": "float32", "units": "kW",
         "value": 0, "description": "+ import / - export", "behavior": {"type": "expr", "expr": flow}},
        {"name": "reactive_power", "table": "input", "address": 14, "datatype": "float32", "units": "kvar",
         "value": 0, "behavior": {"type": "expr", "expr": "active_power * 0.2"}},
        {"name": "power_factor", "table": "input", "address": 16, "datatype": "float32", "value": 1,
         "behavior": {"type": "expr",
                      "expr": "abs(active_power) / max(sqrt(active_power ** 2 + reactive_power ** 2), 1e-9)"}},
        {"name": "frequency", "table": "input", "address": 18, "datatype": "float32", "units": "Hz", "value": hz,
         "behavior": {"type": "random_walk", "min": hz - 0.05, "max": hz + 0.05, "step": 0.005}},
        # energy counters: uint32 (0.1 kWh) and float64 (kWh)
        {"name": "import_energy", "table": "input", "address": 20, "datatype": "uint32", "scale": 0.1,
         "units": "kWh", "value": 0, "behavior": {"type": "counter", "rate": "max(active_power, 0) / 3600"}},
        {"name": "export_energy", "table": "input", "address": 22, "datatype": "uint32", "scale": 0.1,
         "units": "kWh", "value": 0, "behavior": {"type": "counter", "rate": "max(-active_power, 0) / 3600"}},
        {"name": "import_energy_f64", "table": "input", "address": 24, "datatype": "float64", "units": "kWh",
         "value": 0, "behavior": {"type": "counter", "rate": "max(active_power, 0) / 3600"}},
        {"name": "export_energy_f64", "table": "input", "address": 28, "datatype": "float64", "units": "kWh",
         "value": 0, "behavior": {"type": "counter", "rate": "max(-active_power, 0) / 3600"}},
        {"name": "serial_number", "table": "holding", "address": 0, "datatype": "string", "count": 8,
         "access": "r", "value": "SIM00001"},
    ]
    return pts


@template("env_sensor", "Temperature / humidity sensor (generic)", {"temperature_c": 25, "humidity_pct": 50})
def _env_sensor(p):
    t, h = _num(p, "temperature_c"), _num(p, "humidity_pct")
    # addresses left out on purpose: allocated from input 0 upwards
    return [
        {"name": "temperature", "table": "input", "datatype": "int16", "scale": 0.1, "units": "degC", "value": t,
         "behavior": {"type": "sine", "min": t - 3, "max": t + 3, "period": 86400, "phase": -1.57,
                      "noise": 0.1}},
        {"name": "humidity", "table": "input", "datatype": "int16", "scale": 0.1, "units": "%RH", "value": h,
         "behavior": {"type": "follow", "target": f"clamp({h} - (temperature - {t}) * 2, 0, 100)", "tau": 300,
                      "noise": 0.2}},
    ]


@template("custom", "Empty device (define points yourself)", {})
def _custom(p):
    return []


def allocate_addresses(points: List[PointConfig]) -> List[PointConfig]:
    """Give points without an address the next free address of their table.

    Allocation starts after the highest explicit address range of the table (0 when there is none) and
    advances by each point's register count, in point order.
    """
    nxt: Dict[str, int] = {}
    for p in points:
        if p.address is not None:
            nxt[p.table] = max(nxt.get(p.table, 0), p.address + register_count(p))
    out = []
    for p in points:
        if p.address is None:
            a = nxt.get(p.table, 0)
            p = p.model_copy(update={"address": a})
            nxt[p.table] = a + register_count(p)
        out.append(p)
    return out


def expand_device(dev: DeviceConfig) -> List[PointConfig]:
    """Template points merged with the device's own points (same name overrides), addresses allocated."""
    pts: List[Pt] = []
    if dev.template:
        t = TEMPLATES.get(dev.template)
        if t is None:
            raise ValueError(f"{dev.name}: unknown template {dev.template!r} (known: {', '.join(TEMPLATES)})")
        params = {**t["params"], **dev.params}
        pts = t["build"](params)
    result: Dict[str, PointConfig] = {p["name"]: PointConfig.model_validate(p) for p in pts}
    for p in dev.points:
        if p.name in result:
            merged = {**result[p.name].model_dump(exclude_none=True), **p.model_dump(exclude_unset=True)}
            try:
                result[p.name] = PointConfig.model_validate(merged)
            except ValueError as e:
                raise ValueError(f"{dev.name}/{p.name}: {e}") from None
        elif p.table is None:
            raise ValueError(f"{dev.name}/{p.name}: needs a table (no template point of that name)")
        else:
            result[p.name] = p
    for name in dev.removed or []:
        result.pop(name, None)
    final = allocate_addresses(list(result.values()))
    check_layout(dev.name, final)
    return final


def template_catalog() -> List[Dict[str, Any]]:
    return [{"name": t["name"], "title": t["title"], "params": t["params"]} for t in TEMPLATES.values()]

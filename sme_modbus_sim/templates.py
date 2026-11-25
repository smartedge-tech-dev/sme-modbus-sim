"""Built-in device templates.

The mechanism (``@template``, params merge, override by name, ``removed``, catalog, user-local templates) is
``sme_sim_common.templates.TemplateRegistry``; this module plugs in the Modbus hooks (address auto-allocation,
"needs a table" + layout check, byte / word order export) and keeps the module-level API.

The register maps below are ORIGINAL, vendor-neutral layouts invented for this simulator. They do not
reproduce any manufacturer's communication specification (design §5.3, §12 Q6/Q12).

A template is a function ``params -> list[point dict]``. Sign convention: active power > 0 = discharge /
export from the battery, < 0 = charge. Cross-device references use ``pv("Device", "point")`` and are
enabled through params such as ``bms_device`` / ``pcs_device`` (empty = standalone).

User-local templates (design 5.3.1) are YAML files in the user template directory; a user template
overrides the built-in template of the same name (see ``load_user_templates``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from sme_sim_common.templates import NAME_RE, TemplateRegistry, env_var_for, escape_braces, substitute  # noqa: F401

from .config import DeviceConfig, PointConfig, check_layout, register_count

Pt = Dict[str, Any]
APP_NAME = "sme-modbus-sim"


def allocate_addresses(points: List[PointConfig]) -> List[PointConfig]:
    """Give points without an address the next free address of their table.

    Allocation starts after the highest explicit address range of the table (0 when there is none) and
    advances by each point's register count, in point order.
    """
    nxt: Dict[Any, int] = {}
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


def _finalize(owner: str, points: List[PointConfig]) -> List[PointConfig]:
    return allocate_addresses(points)


def _validate(owner: str, points: List[PointConfig]) -> None:
    """Every merged point needs a table (an override without one must match a template point), then the
    address layout / scale_ref check."""
    for p in points:
        if p.table is None:
            raise ValueError(f"{owner}/{p.name}: needs a table (no template point of that name)")
    check_layout(owner, points)


def _export(dev: DeviceConfig, d: Pt) -> Pt:
    """``device_as_template``: copy a non-default device byte / word order onto points that do not set their
    own; enum label keys back to int (``mode="json"`` dumps them as text)."""
    for k in ("byte_order", "word_order"):
        if getattr(dev, k) != "big" and k not in d:
            d[k] = getattr(dev, k)
    if "labels" in d:
        d["labels"] = {int(k): v for k, v in d["labels"].items()}
    return d


registry: TemplateRegistry[PointConfig] = TemplateRegistry(
    PointConfig, _validate, app_name=APP_NAME, finalize_hook=_finalize, export_hook=_export,
    extra_keys=("identification",))  # templates may carry FC2B/0E identification defaults
template = registry.template  # decorator: @template(name, title, params, description=None)
TEMPLATES: Dict[str, Dict[str, Any]] = registry.builtins  # same dict objects as the registry's (live views)
USER_TEMPLATES: Dict[str, Dict[str, Any]] = registry.user_templates
USER_TEMPLATE_ERRORS: List[Dict[str, str]] = registry.user_errors
ENV_TEMPLATES_DIR = registry.env_var  # SME_MODBUS_SIM_TEMPLATES


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


# ---------------------------------------------------------------- SunSpec templates
#
# Generated from the public SunSpec model definitions (Apache-2.0), see ``sunspec.py``. Implements SunSpec
# Modbus models; not affiliated with, endorsed by, or certified by SunSpec Alliance.

SUNSPEC_NOTE = ("Generated from the public SunSpec model definitions (Apache-2.0); implements SunSpec Modbus "
                "models. Not affiliated with, endorsed by, or certified by SunSpec Alliance.")


def _sunspec_common(model: str, serial: Any) -> Dict[str, Any]:
    from . import __version__

    return {"m1_Mn": "SmartEdge", "m1_Md": model, "m1_Vr": __version__, "m1_SN": str(serial), "m1_DA": 1}


def _expr(e: str) -> Dict[str, Any]:
    return {"behavior": {"type": "expr", "expr": e}}


def _walk(center: float, rel: float, step: float) -> Dict[str, Any]:
    return {"behavior": {"type": "random_walk", "min": center * (1 - rel), "max": center * (1 + rel),
                         "step": step}}


def _counter(rate: str) -> Dict[str, Any]:
    return {"behavior": {"type": "counter", "rate": rate}}


@template("sunspec_pv_inverter", "SunSpec PV inverter (models 1, 103, 120-123, 160)",
          {"rated_w": 10000, "mppt_count": 2, "ac_voltage_v": 230, "frequency_hz": 50, "dc_voltage_v": 600,
           "serial_number": "SIM-PV-0001", "base_address": 40000})
def _sunspec_pv_inverter(p):
    from . import sunspec as ss

    rated, vac, hz = _num(p, "rated_w"), _num(p, "ac_voltage_v"), _num(p, "frequency_hz")
    vdc = _num(p, "dc_voltage_v")
    n = max(1, int(p["mppt_count"]))
    w_sf = ss.sf_for(rated * 1.3)
    a_rtg = rated / (3 * vac)
    a_sf = ss.sf_for(a_rtg * 1.3, 65535, -2)
    dca_sf = ss.sf_for(rated / vdc * 1.3, 65535, -2)
    vals: Dict[str, Any] = {
        **_sunspec_common("SME-SIM-PV", p["serial_number"]),
        # 103 three-phase inverter
        "m103_A_SF": a_sf, "m103_V_SF": -1, "m103_W_SF": w_sf, "m103_Hz_SF": -2, "m103_VA_SF": w_sf,
        "m103_VAr_SF": w_sf, "m103_PF_SF": -1, "m103_WH_SF": 0, "m103_DCA_SF": dca_sf, "m103_DCV_SF": -1,
        "m103_DCW_SF": w_sf, "m103_Tmp_SF": -1, "m103_PhVphA": vac, "m103_PhVphB": vac, "m103_PhVphC": vac,
        "m103_Hz": hz, "m103_PF": 100, "m103_TmpCab": 25, "m103_St": 2, "m103_Evt1": 0, "m103_DCV": vdc,
        # 120 nameplate
        "m120_DERTyp": 4, "m120_WRtg": rated, "m120_WRtg_SF": w_sf, "m120_VARtg": rated, "m120_VARtg_SF": w_sf,
        "m120_VArRtgQ1": rated * 0.44, "m120_VArRtgQ2": rated * 0.44, "m120_VArRtgQ3": -rated * 0.44,
        "m120_VArRtgQ4": -rated * 0.44, "m120_VArRtg_SF": w_sf, "m120_ARtg": a_rtg, "m120_ARtg_SF": a_sf,
        "m120_PFRtgQ1": 0.8, "m120_PFRtgQ2": 0.8, "m120_PFRtgQ3": -0.8, "m120_PFRtgQ4": -0.8,
        "m120_PFRtg_SF": -2,
        # 121 basic settings
        "m121_WMax": rated, "m121_WMax_SF": w_sf, "m121_VRef": vac, "m121_VRef_SF": 0, "m121_VRefOfs": 0,
        "m121_VRefOfs_SF": 0,
        # 122 measurements / status
        "m122_PVConn": 3, "m122_StorConn": 0, "m122_ECPConn": 1, "m122_ActWh": 0,
        # 123 immediate controls
        "m123_Conn": 1, "m123_WMaxLimPct": 100, "m123_WMaxLimPct_SF": 0, "m123_WMaxLim_Ena": 0,
        "m123_OutPFSet": 1, "m123_OutPFSet_SF": -3, "m123_OutPFSet_Ena": 0,
        # 160 MPPT
        "m160_DCA_SF": dca_sf, "m160_DCV_SF": -1, "m160_DCW_SF": w_sf, "m160_DCWH_SF": 0, "m160_Evt": 0, "m160_N": n,
    }
    cap = f"({rated} * (m123_WMaxLimPct / 100 if m123_WMaxLim_Ena == 1 else 1))"
    daylight = f"{rated} * max(0, sin(pi * (hour - 6) / 12))"
    ov: Dict[str, Dict[str, Any]] = {
        "m103_W": _expr(f"0 if m123_Conn != 1 else max(0, min({daylight} * (1 + noise(0.02)), {cap}))"),
        "m103_WH": _counter("max(m103_W, 0) / 3600"),
        "m103_Hz": _walk(hz, 0.001, 0.005),
        "m103_PF": _expr("(abs(m123_OutPFSet) if m123_OutPFSet_Ena == 1 else 1) * 100"),
        "m103_VA": _expr("abs(m103_W) / max(abs(m103_PF) / 100, 0.01)"),
        "m103_VAr": _expr("sqrt(max(m103_VA ** 2 - m103_W ** 2, 0))"),
        "m103_A": _expr("m103_W / max((m103_PhVphA + m103_PhVphB + m103_PhVphC) / 3, 1)"),
        "m103_DCW": _expr("m103_W / 0.97"),
        "m103_DCV": _walk(vdc, 0.02, vdc * 0.002),
        "m103_DCA": _expr("m103_DCW / max(m103_DCV, 1)"),
        "m103_TmpCab": {"behavior": {"type": "follow", "target": f"25 + m103_W / {rated} * 20", "tau": 600}},
        "m103_St": _expr("1 if m123_Conn != 1 else (4 if m103_W > 0 else 2)"),
        "m122_PVConn": _expr("(7 if m103_W > 0 else 3) if m123_Conn == 1 else 2"),
        "m122_ECPConn": _expr("int(m123_Conn == 1)"),
        "m122_ActWh": _counter("max(m103_W, 0) / 3600"),
        "m123_Conn": {"min": 0, "max": 1},
        "m123_WMaxLimPct": {"min": 0, "max": 100},
        "m123_WMaxLim_Ena": {"min": 0, "max": 1},
        "m123_OutPFSet": {"min": -1, "max": 1},
    }
    for ph, pair in (("A", "AB"), ("B", "BC"), ("C", "CA")):
        ov[f"m103_PhVph{ph}"] = _walk(vac, 0.02, vac * 0.001)
        ov[f"m103_Aph{ph}"] = _expr(f"m103_W / 3 / max(m103_PhVph{ph}, 1)")
        ov[f"m103_PPVph{pair}"] = _expr(f"(m103_PhVph{pair[0]} + m103_PhVph{pair[1]}) / 2 * sqrt(3)")
    for i in range(1, n + 1):
        m = f"m160_module{i}_"
        vals.update({f"{m}ID": i, f"{m}IDStr": f"MPPT{i}", f"{m}DCV": vdc, f"{m}DCSt": 2, f"{m}DCEvt": 0,
                     f"{m}Tmp": 25})
        ov.update({f"{m}DCW": _expr(f"m103_DCW / {n}"), f"{m}DCV": _walk(vdc, 0.03, vdc * 0.002),
                   f"{m}DCA": _expr(f"{m}DCW / max({m}DCV, 1)"), f"{m}DCWH": _counter(f"{m}DCW / 3600"),
                   f"{m}DCSt": _expr("m103_St"), f"{m}Tmp": _expr("round(m103_TmpCab)")})
    return ss.build_points([1, 103, 120, 121, 122, 123, 160], int(p["base_address"]), vals, ov,
                           {160: {"module": n}})


@template("sunspec_meter", "SunSpec three-phase meter (models 1, 203)",
          {"base_load_w": 5000, "voltage_v": 230, "frequency_hz": 50, "pv_device": "",
           "serial_number": "SIM-MTR-0001", "base_address": 40000})
def _sunspec_meter(p):
    from . import sunspec as ss

    load, v, hz = _num(p, "base_load_w"), _num(p, "voltage_v"), _num(p, "frequency_hz")
    pv = _ref(p.get("pv_device"), "m103_W", "0")
    w_sf = ss.sf_for(load * 3)
    a_sf = ss.sf_for(load * 3 / v, 32767, -2)
    vals: Dict[str, Any] = {
        **_sunspec_common("SME-SIM-METER", p["serial_number"]),
        "m203_A_SF": a_sf, "m203_V_SF": -1, "m203_Hz_SF": -2, "m203_W_SF": w_sf, "m203_VA_SF": w_sf,
        "m203_VAR_SF": w_sf, "m203_PF_SF": -1, "m203_TotWh_SF": 0, "m203_Hz": hz, "m203_PF": 100,
        "m203_Evt": 0, "m203_TotWhImp": 0, "m203_TotWhExp": 0,
    }
    # grid point flow: load (daily profile) - PV output; > 0 = import
    flow = f"{load} * (1 + 0.3 * sin(2 * pi * (hour - 8) / 24)) + noise({load * 0.02}) - ({pv})"
    ov: Dict[str, Dict[str, Any]] = {
        "m203_PhV": _expr("(m203_PhVphA + m203_PhVphB + m203_PhVphC) / 3"),
        "m203_PPV": _expr("m203_PhV * sqrt(3)"),
        "m203_Hz": _walk(hz, 0.001, 0.005),
        "m203_W": _expr(flow),
        "m203_VAR": _expr("m203_W * 0.2"),
        "m203_VA": _expr("sqrt(m203_W ** 2 + m203_VAR ** 2)"),
        "m203_PF": _expr("100 * abs(m203_W) / max(m203_VA, 1e-9)"),
        "m203_A": _expr("m203_VA / max(m203_PhV, 1)"),
        "m203_TotWhImp": _counter("max(m203_W, 0) / 3600"),
        "m203_TotWhExp": _counter("max(-m203_W, 0) / 3600"),
    }
    for ph, pair in (("A", "AB"), ("B", "BC"), ("C", "CA")):
        vals[f"m203_PhVph{ph}"] = v
        ov[f"m203_PhVph{ph}"] = _walk(v, 0.02, v * 0.001)
        ov[f"m203_PhVph{pair}"] = _expr(f"(m203_PhVph{pair[0]} + m203_PhVph{pair[1]}) / 2 * sqrt(3)")
        ov[f"m203_Wph{ph}"] = _expr("m203_W / 3")
        ov[f"m203_VARph{ph}"] = _expr("m203_VAR / 3")
        ov[f"m203_VAph{ph}"] = _expr("m203_VA / 3")
        ov[f"m203_PFph{ph}"] = _expr("m203_PF")
        ov[f"m203_Aph{ph}"] = _expr(f"m203_VA / 3 / max(m203_PhVph{ph}, 1)")
    return ss.build_points([1, 203], int(p["base_address"]), vals, ov)


@template("sunspec_der", "SunSpec DER (models 1, 701-705)",
          {"rated_w": 10000, "ac_voltage_v": 230, "frequency_hz": 50, "vv_curves": 3, "vv_points": 4,
           "serial_number": "SIM-DER-0001", "base_address": 40000})
def _sunspec_der(p):
    from . import sunspec as ss

    rated, vac, hz = _num(p, "rated_w"), _num(p, "ac_voltage_v"), _num(p, "frequency_hz")
    ncrv, npt = max(1, int(p["vv_curves"])), max(1, int(p["vv_points"]))
    w_sf = ss.sf_for(rated * 1.3)
    a_rtg = rated / (3 * vac)
    a_sf = ss.sf_for(a_rtg * 1.3, 32767, -2)
    vals: Dict[str, Any] = {
        **_sunspec_common("SME-SIM-DER", p["serial_number"]),
        # 701 DER AC measurement
        "m701_ACType": 2, "m701_St": 1, "m701_InvSt": 7, "m701_ConnSt": 1, "m701_Alrm": 0, "m701_DERMode": 0,
        "m701_LNV": vac, "m701_LLV": vac * 3 ** 0.5, "m701_Hz": hz, "m701_PF": 1, "m701_TmpCab": 25,
        "m701_A_SF": a_sf, "m701_V_SF": -1, "m701_Hz_SF": -2, "m701_W_SF": w_sf, "m701_PF_SF": -3,
        "m701_VA_SF": w_sf, "m701_Var_SF": w_sf, "m701_TotWh_SF": 0, "m701_TotVarh_SF": 0, "m701_Tmp_SF": -1,
        # 702 capacity (ratings + adjustable settings)
        "m702_WMaxRtg": rated, "m702_VAMaxRtg": rated, "m702_VarMaxInjRtg": rated * 0.44,
        "m702_VarMaxAbsRtg": rated * 0.44, "m702_WChaRteMaxRtg": rated, "m702_WDisChaRteMaxRtg": rated,
        "m702_VNomRtg": vac, "m702_VMaxRtg": vac * 1.1, "m702_VMinRtg": vac * 0.88, "m702_AMaxRtg": a_rtg,
        "m702_NorOpCatRtg": 1, "m702_AbnOpCatRtg": 2, "m702_WMax": rated, "m702_VAMax": rated,
        "m702_VarMaxInj": rated * 0.44, "m702_VarMaxAbs": rated * 0.44, "m702_WChaRteMax": rated,
        "m702_WDisChaRteMax": rated, "m702_VNom": vac, "m702_VMax": vac * 1.1, "m702_VMin": vac * 0.88,
        "m702_AMax": a_rtg, "m702_W_SF": w_sf, "m702_VA_SF": w_sf, "m702_Var_SF": w_sf, "m702_V_SF": -1,
        "m702_A_SF": a_sf, "m702_PF_SF": -3,
        # 703 enter service
        "m703_ES": 1, "m703_ESVHi": 105, "m703_ESVLo": 91.7, "m703_ESHzHi": hz * 1.002, "m703_ESHzLo": hz * 0.99,
        "m703_ESDlyTms": 300, "m703_ESRndTms": 0, "m703_ESRmpTms": 300, "m703_ESDlyRemTms": 0,
        "m703_V_SF": -1, "m703_Hz_SF": -2,
        # 704 AC controls
        "m704_PFWInjEna": 0, "m704_PFWAbsEna": 0, "m704_WMaxLimPctEna": 0, "m704_WMaxLimPct": 100,
        "m704_WSetEna": 0, "m704_WSetMod": 1, "m704_WSet": 0, "m704_WSetPct": 0, "m704_VarSetEna": 0,
        "m704_VarSetMod": 4, "m704_VarSetPri": 0, "m704_VarSet": 0, "m704_VarSetPct": 0, "m704_WRmp": 100,
        "m704_WRmpRef": 1, "m704_AntiIslEna": 1, "m704_PFWInj_PF": 1, "m704_PFWInj_Ext": 0,
        "m704_PFWAbs_PF": 1, "m704_PFWAbs_Ext": 1, "m704_PF_SF": -3, "m704_WMaxLimPct_SF": 0,
        "m704_WSet_SF": 0, "m704_WSetPct_SF": -1, "m704_VarSet_SF": 0, "m704_VarSetPct_SF": -1,
        # 705 volt-var (curve registers only; the curve is not executed)
        "m705_Ena": 0, "m705_AdptCrvReq": 0, "m705_AdptCrvRslt": 1, "m705_V_SF": -2, "m705_DeptRef_SF": -2,
        "m705_RspTms_SF": 0,
    }
    wmax = "m702_WMax"
    pv_avail = f"{rated} * max(0, sin(pi * (hour - 6) / 12))"
    setpoint = f"(m704_WSet if m704_WSetMod == 1 else m704_WSetPct / 100 * {wmax})"
    raw = f"({setpoint} if m704_WSetEna == 1 else {pv_avail})"
    cap = f"({wmax} * (m704_WMaxLimPct / 100 if m704_WMaxLimPctEna == 1 else 1))"
    target = f"(0 if m703_ES != 1 else max(-m702_WChaRteMax, min({raw}, {cap})))"
    up = f"max(m704_WRmp, 1) / 100 * {wmax}"
    var_sp = ("(m704_VarSet if m704_VarSetMod == 4 else m704_VarSetPct / 100 * ("
              f"{wmax} if m704_VarSetMod == 0 else m702_VAMax if m704_VarSetMod == 3 else "
              "sqrt(max(m702_VAMax ** 2 - m701_W ** 2, 0)) if m704_VarSetMod == 2 else m702_VarMaxInj))")
    ov: Dict[str, Dict[str, Any]] = {
        # WRmp (% of WMax per second) limits increases; decreases follow (almost) at once
        "m701_W": {"behavior": {"type": "ramp", "target": target,
                                "rate": f"({up}) if {target} > m701_W else 10 * {wmax}"}},
        "m701_Var": _expr("0 if m703_ES != 1 or m704_VarSetEna != 1 else "
                          f"max(-m702_VarMaxAbs, min({var_sp}, m702_VarMaxInj))"),
        "m701_VA": _expr("sqrt(m701_W ** 2 + m701_Var ** 2)"),
        "m701_PF": _expr("1 if m701_VA < 1e-6 else abs(m701_W) / m701_VA"),
        "m701_A": _expr("m701_VA / 3 / max(m701_LNV, 1)"),
        "m701_LNV": _expr("(m701_VL1 + m701_VL2 + m701_VL3) / 3"),
        "m701_LLV": _expr("(m701_VL1L2 + m701_VL2L3 + m701_VL3L1) / 3"),
        "m701_Hz": _walk(hz, 0.001, 0.005),
        "m701_TotWhInj": _counter("max(m701_W, 0) / 3600"),
        "m701_TotWhAbs": _counter("max(-m701_W, 0) / 3600"),
        "m701_TotVarhInj": _counter("max(m701_Var, 0) / 3600"),
        "m701_TotVarhAbs": _counter("max(-m701_Var, 0) / 3600"),
        "m701_TmpCab": {"behavior": {"type": "follow", "target": f"25 + abs(m701_W) / {rated} * 20", "tau": 600}},
        "m701_St": _expr("int(m703_ES == 1)"),
        "m701_ConnSt": _expr("int(m703_ES == 1)"),
        "m701_InvSt": _expr(f"0 if m703_ES != 1 else (4 if m704_WMaxLimPctEna == 1 and m701_W > 0 "
                            f"and m701_W >= {cap} - 1 else (3 if abs(m701_W) > 0 else 7))"),
        "m703_ES": {"min": 0, "max": 1},
        "m704_WMaxLimPctEna": {"min": 0, "max": 1}, "m704_WMaxLimPct": {"min": 0, "max": 100},
        "m704_WSetEna": {"min": 0, "max": 1}, "m704_WSetMod": {"min": 0, "max": 1},
        "m704_WSetPct": {"min": -100, "max": 100}, "m704_VarSetEna": {"min": 0, "max": 1},
        "m704_VarSetMod": {"min": 0, "max": 4}, "m704_VarSetPct": {"min": -100, "max": 100},
        "m704_WRmp": {"min": 1, "max": 10000},
        "m705_Ena": {"min": 0, "max": 1}, "m705_AdptCrvReq": {"min": 0, "max": ncrv},
    }
    for i, (ln, pair) in enumerate((("L1", "L1L2"), ("L2", "L2L3"), ("L3", "L3L1"))):
        nxt = ("L2", "L3", "L1")[i]
        vals[f"m701_V{ln}"] = vac
        ov[f"m701_V{ln}"] = _walk(vac, 0.02, vac * 0.001)
        ov[f"m701_V{pair}"] = _expr(f"(m701_V{ln} + m701_V{nxt}) / 2 * sqrt(3)")
        ov[f"m701_W{ln}"] = _expr("m701_W / 3")
        ov[f"m701_VA{ln}"] = _expr("m701_VA / 3")
        ov[f"m701_Var{ln}"] = _expr("m701_Var / 3")
        ov[f"m701_PF{ln}"] = _expr("m701_PF")
        ov[f"m701_A{ln}"] = _expr(f"m701_VA / 3 / max(m701_V{ln}, 1)")
        ov[f"m701_TotWhInj{ln}"] = _counter("max(m701_W, 0) / 3 / 3600")
        ov[f"m701_TotWhAbs{ln}"] = _counter("max(-m701_W, 0) / 3 / 3600")
    # illustrative default volt-var curve (V in % of VNom, Var in % per DeptRef = VAR_MAX_PCT)
    span = max(npt - 1, 1)
    v_pts = [92, 98, 102, 108] if npt == 4 else [round(90 + 20 * k / span, 2) for k in range(npt)]
    q_pts = [44, 0, 0, -44] if npt == 4 else [round(44 - 88 * k / span, 2) for k in range(npt)]
    for c in range(1, ncrv + 1):
        m = f"m705_Crv{c}_"
        vals.update({f"{m}ActPt": npt, f"{m}DeptRef": 1, f"{m}Pri": 1, f"{m}VRef": 100, f"{m}VRefAutoEna": 0,
                     f"{m}RspTms": 10, f"{m}ReadOnly": int(c == 1)})
        for k in range(1, npt + 1):
            vals.update({f"{m}Pt{k}_V": v_pts[k - 1], f"{m}Pt{k}_Var": q_pts[k - 1]})
    return ss.build_points([1, 701, 702, 703, 704, 705], int(p["base_address"]), vals, ov,
                           {705: {"Crv": ncrv, "Pt": npt}})


@template("sunspec_battery", "SunSpec battery (models 1, 802, 803)",
          {"capacity_wh": 10000, "max_w": 5000, "dc_voltage_v": 400, "initial_soc": 50, "string_count": 2,
           "der_device": "", "serial_number": "SIM-BAT-0001", "base_address": 40000})
def _sunspec_battery(p):
    from . import sunspec as ss

    wh, wmax, vdc = _num(p, "capacity_wh"), _num(p, "max_w"), _num(p, "dc_voltage_v")
    n = max(1, int(p["string_count"]))
    w_sf = ss.sf_for(wmax * 1.3)
    amax = wmax / vdc
    a_sf = ss.sf_for(amax * 1.3, 32767, -2)
    vals: Dict[str, Any] = {
        **_sunspec_common("SME-SIM-BATTERY", p["serial_number"]),
        # 802 battery base
        "m802_AHRtg": wh / vdc, "m802_AHRtg_SF": ss.sf_for(wh / vdc, 65535, -1),
        "m802_WHRtg": wh, "m802_WHRtg_SF": ss.sf_for(wh, 65535),
        "m802_WChaRteMax": wmax, "m802_WDisChaRteMax": wmax, "m802_WChaDisChaMax_SF": ss.sf_for(wmax, 65535),
        "m802_SoCMax": 95, "m802_SoCMin": 5, "m802_SoC": _num(p, "initial_soc"), "m802_SoC_SF": -1,
        "m802_DoD_SF": -1, "m802_SoH": 100, "m802_SoH_SF": -1, "m802_NCyc": 0, "m802_LocRemCtl": 0,
        "m802_Typ": 4, "m802_State": 3, "m802_V": vdc, "m802_VMax": vdc * 1.15, "m802_VMin": vdc * 0.8,
        "m802_V_SF": -1, "m802_CellV_SF": -3, "m802_A_SF": a_sf, "m802_AChaMax": amax, "m802_ADisChaMax": amax,
        "m802_AMax_SF": a_sf, "m802_W_SF": w_sf, "m802_SetOp": 1, "m802_SetInvState": 3,
        # 803 lithium-ion bank
        "m803_NStr": n, "m803_CellV_SF": -3, "m803_ModTmp_SF": -1, "m803_A_SF": a_sf, "m803_SoH_SF": -1,
        "m803_SoC_SF": -1, "m803_V_SF": -1,
    }
    # W > 0 = discharging (simulator convention, same sign as model 701 W where generation is positive). Demo
    # profile: charge by day, discharge at night; or follow the AC power of a linked sunspec_der device.
    demand = _ref(p.get("der_device"), "m701_W", f"-{wmax} * 0.6 * sin(2 * pi * (hour - 6) / 24)")
    req = f"max(-m802_WChaRteMax, min({demand}, m802_WDisChaRteMax))"
    ov: Dict[str, Dict[str, Any]] = {
        "m802_W": _expr(f"0 if m802_SetOp == 2 else ({req} if ({req} > 0 and m802_SoC > m802_SoCMin) "
                        f"or ({req} < 0 and m802_SoC < m802_SoCMax) else 0)"),
        "m802_SoC": {"behavior": {"type": "counter", "rate": f"-m802_W / {wh} * 100 / 3600"}, "min": 0, "max": 100},
        "m802_DoD": _expr("100 - m802_SoC"),
        "m802_V": _expr(f"{vdc} * (0.95 + 0.1 * m802_SoC / 100)"),
        "m802_A": _expr("m802_W / max(m802_V, 1)"),
        "m802_ChaSt": _expr("3 if m802_W > 0 else (4 if m802_W < 0 else (2 if m802_SoC <= m802_SoCMin else "
                            "(5 if m802_SoC >= m802_SoCMax else 6)))"),
        "m802_State": _expr("1 if m802_SetOp == 2 else 3"),
        "m802_Hb": _counter("1"),
        "m802_CellVAvg": _expr("3.0 + 0.4 * m802_SoC / 100"),
        "m802_CellVMax": _expr("m802_CellVAvg + 0.02"),
        "m802_CellVMin": _expr("m802_CellVAvg - 0.02"),
        "m802_SetOp": {"min": 1, "max": 2}, "m802_SetInvState": {"min": 1, "max": 3},
        "m802_SocRsvMax": {"min": 0, "max": 100}, "m802_SoCRsvMin": {"min": 0, "max": 100},
        "m803_NStrCon": _expr(f"0 if m802_SetOp == 2 else {n}"),
        "m803_ModTmpAvg": {"behavior": {"type": "follow", "target": f"25 + abs(m802_W) / {wmax} * 10",
                                        "tau": 900}},
        "m803_ModTmpMax": _expr("m803_ModTmpAvg + 1"),
        "m803_ModTmpMin": _expr("m803_ModTmpAvg - 1"),
        "m803_StrVAvg": _expr("m802_V"),
        "m803_StrAAvg": _expr(f"m802_A / {n}"),
    }
    for i in range(1, n + 1):
        m = f"m803_string{i}_"
        vals.update({f"{m}StrNMod": 8, f"{m}StrSt": 0, f"{m}StrSoH": 100, f"{m}StrEvt1": 0})
        ov.update({f"{m}StrSoC": _expr("m802_SoC"), f"{m}StrA": _expr(f"m802_A / {n}"),
                   f"{m}StrCellVAvg": _expr("m802_CellVAvg"), f"{m}StrCellVMax": _expr("m802_CellVMax"),
                   f"{m}StrCellVMin": _expr("m802_CellVMin"), f"{m}StrModTmpAvg": _expr("m803_ModTmpAvg"),
                   f"{m}StrModTmpMax": _expr("m803_ModTmpMax"), f"{m}StrModTmpMin": _expr("m803_ModTmpMin"),
                   f"{m}StrConSt": _expr("int(m802_SetOp != 2)")})
    return ss.build_points([1, 802, 803], int(p["base_address"]), vals, ov, {803: {"string": n}})


for _name in ("sunspec_pv_inverter", "sunspec_meter", "sunspec_der", "sunspec_battery"):
    TEMPLATES[_name]["description"] = SUNSPEC_NOTE


# ---------------------------------------------------------------- module-level API (delegates to ``registry``)
#
# User-local templates (design §5.3.1): ``<dir>/*.yaml`` / ``*.yml`` with name / title / description / params /
# points; format and ``{param}`` substitution as documented in ``sme_sim_common.templates``.


def get_template(name: str) -> Optional[Dict[str, Any]]:
    """User template first (it overrides a built-in of the same name), then built-in."""
    return registry.get(name)


def expand_device(dev: DeviceConfig) -> List[PointConfig]:
    """Template points merged with the device's own points (same name overrides), addresses allocated."""
    return registry.expand_device(dev)


def template_catalog() -> List[Dict[str, Any]]:
    """Built-ins (replaced in place by a user template of the same name), then user-only templates."""
    return registry.catalog()


def default_templates_dir() -> Path:
    """``$SME_MODBUS_SIM_TEMPLATES`` or ``~/.sme-modbus-sim/templates``."""
    return registry.default_templates_dir()


def templates_dir() -> Path:
    """The directory last loaded (or the default one)."""
    return registry.templates_dir()


def clear_user_templates() -> None:
    """Forget all user templates (the directory is kept for a later reload)."""
    registry.clear_user_templates()


def load_user_templates(directory: Optional[Union[Path, str]] = None) -> List[Dict[str, str]]:
    """(Re)load the user template directory; bad files are reported as ``{"path", "error"}``, never fatal."""
    return registry.load_user_templates(directory)


def device_as_template(dev: DeviceConfig, name: str, title: Optional[str] = None,
                       description: Optional[str] = None) -> str:
    """YAML text of a user template built from a device's expanded points (literal values, no params)."""
    return registry.device_as_template(dev, name, title, description)

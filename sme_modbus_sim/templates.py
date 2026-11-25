"""Built-in device templates.

Mechanism (``@template``, params merge, override by name, ``removed``, ``template_catalog``) follows
sme-bac-sim ``sme_bacnet_sim/templates.py`` @ ebd27ec; instance auto-assignment became address auto-allocation.

The register maps below are ORIGINAL, vendor-neutral layouts invented for this simulator. They do not
reproduce any manufacturer's communication specification (design §5.3, §12 Q6/Q12).

A template is a function ``params -> list[point dict]``. Sign convention: active power > 0 = discharge /
export from the battery, < 0 = charge. Cross-device references use ``pv("Device", "point")`` and are
enabled through params such as ``bms_device`` / ``pcs_device`` (empty = standalone).

User-local templates (design 5.3.1) are YAML files in the user template directory; a user template
overrides the built-in template of the same name (see ``load_user_templates``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from pydantic import ValidationError

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


for _name in ("sunspec_pv_inverter", "sunspec_meter"):
    TEMPLATES[_name]["description"] = SUNSPEC_NOTE


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


def get_template(name: str) -> Optional[Dict[str, Any]]:
    """User template first (it overrides a built-in of the same name), then built-in."""
    return USER_TEMPLATES.get(name) or TEMPLATES.get(name)


def expand_device(dev: DeviceConfig) -> List[PointConfig]:
    """Template points merged with the device's own points (same name overrides), addresses allocated."""
    pts: List[Pt] = []
    if dev.template:
        t = get_template(dev.template)
        if t is None:
            known = ", ".join(dict.fromkeys([*TEMPLATES, *USER_TEMPLATES]))
            raise ValueError(f"{dev.name}: unknown template {dev.template!r} (known: {known})")
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
    """Built-ins (replaced in place by a user template of the same name), then user-only templates."""
    out = []
    for name in dict.fromkeys([*TEMPLATES, *USER_TEMPLATES]):
        t = get_template(name)
        e = {"name": name, "title": t["title"], "params": t["params"], "source": t.get("source", "builtin")}
        if t.get("description"):
            e["description"] = t["description"]
        if "path" in t:
            e["path"] = t["path"]
            e["overrides_builtin"] = name in TEMPLATES
        out.append(e)
    return out


# ---------------------------------------------------------------- user-local templates (design §5.3.1)
#
# A user template file (``<dir>/*.yaml`` / ``*.yml``) is a mapping:
#
#   name: my_inverter            # default: the file name without extension
#   title: My inverter           # default: name
#   description: free text
#   params: {rated_kw: 10}       # defaults, overridable per device with ``params:``
#   points: [...]                # same schema as a device's ``points:``
#
# Parameter substitution happens only inside string values: ``str.format`` with the merged params
# (``"clamp(p, 0, {rated_kw})"``). A string that is exactly one placeholder (``max: "{rated_kw}"``) takes the
# parameter value with its type. Literal braces are written ``{{`` / ``}}``.

ENV_TEMPLATES_DIR = "SME_MODBUS_SIM_TEMPLATES"
USER_TEMPLATES: Dict[str, Dict[str, Any]] = {}
USER_TEMPLATE_ERRORS: List[Dict[str, str]] = []
_user_dir: Optional[Path] = None
_TEMPLATE_KEYS = {"name", "title", "description", "params", "points"}
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_ONE_PLACEHOLDER = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def default_templates_dir() -> Path:
    env = os.environ.get(ENV_TEMPLATES_DIR)
    return Path(env).expanduser() if env else Path.home() / ".sme-modbus-sim" / "templates"


def templates_dir() -> Path:
    """The directory last loaded (or the default one)."""
    return _user_dir or default_templates_dir()


def substitute(v: Any, params: Dict[str, Any]) -> Any:
    """``{param}`` substitution inside string values (recursively through lists / mappings)."""
    if isinstance(v, str):
        m = _ONE_PLACEHOLDER.match(v)
        if m:
            if m.group(1) not in params:
                raise ValueError(f"unknown parameter {m.group(1)!r} in {v!r}")
            return params[m.group(1)]
        try:
            return v.format(**params)
        except KeyError as e:
            raise ValueError(f"unknown parameter {e.args[0]!r} in {v!r}") from None
        except (IndexError, ValueError) as e:
            raise ValueError(f"bad placeholder in {v!r}: {e} (write literal braces as {{{{ and }}}})") from None
    if isinstance(v, list):
        return [substitute(x, params) for x in v]
    if isinstance(v, dict):
        return {k: substitute(x, params) for k, x in v.items()}
    return v


def _user_template(data: Any, path: Path) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("expected a mapping with name / title / params / points")
    unknown = sorted(set(data) - _TEMPLATE_KEYS)
    if unknown:
        raise ValueError(f"unknown key(s) {unknown} (allowed: {', '.join(sorted(_TEMPLATE_KEYS))})")
    name = str(data.get("name") or path.stem)
    if not NAME_RE.match(name):
        raise ValueError(f"invalid template name {name!r} (letters, digits, _ . -)")
    params = data.get("params") or {}
    points = data.get("points") or []
    if not isinstance(params, dict):
        raise ValueError("params must be a mapping")
    if not isinstance(points, list) or not all(isinstance(p, dict) for p in points):
        raise ValueError("points must be a list of mappings")

    def build(p: Dict[str, Any], _points=points) -> List[Pt]:
        return [substitute(pt, p) for pt in _points]

    try:  # check with the default params: point schema, names, layout
        pts = [PointConfig.model_validate(p) for p in build(dict(params))]
    except ValidationError as e:
        raise ValueError("; ".join(f"points.{'.'.join(map(str, err['loc']))}: "
                                   f"{str(err['msg']).removeprefix('Value error, ')}" for err in e.errors())) from None
    missing = [p.name for p in pts if p.table is None]
    if missing:
        raise ValueError(f"points {missing} need a table")
    names = [p.name for p in pts]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"duplicate point names {dup}")
    check_layout(name, allocate_addresses(pts))
    return {"name": name, "title": str(data.get("title") or name), "description": data.get("description"),
            "params": params, "build": build, "source": "user", "path": str(path)}


def clear_user_templates() -> None:
    """Forget all user templates (the directory is kept for a later reload)."""
    USER_TEMPLATES.clear()
    USER_TEMPLATE_ERRORS.clear()


def load_user_templates(directory: Optional[Path | str] = None) -> List[Dict[str, str]]:
    """(Re)load the user template directory (default: ``templates_dir()``). Bad files are skipped and
    reported as ``{"path", "error"}``, never fatal; a missing directory is not an error."""
    global _user_dir
    _user_dir = Path(directory).expanduser() if directory else templates_dir()
    loaded: Dict[str, Dict[str, Any]] = {}
    errs: List[Dict[str, str]] = []
    files = sorted([*_user_dir.glob("*.yaml"), *_user_dir.glob("*.yml")]) if _user_dir.is_dir() else []
    for f in files:
        try:
            t = _user_template(yaml.safe_load(f.read_text(encoding="utf-8")), f)
            if t["name"] in loaded:
                raise ValueError(f"template {t['name']!r} already defined in {loaded[t['name']]['path']}")
            loaded[t["name"]] = t
        except (OSError, yaml.YAMLError, ValueError) as e:
            errs.append({"path": str(f), "error": str(e)})
    USER_TEMPLATES.clear()
    USER_TEMPLATES.update(loaded)
    USER_TEMPLATE_ERRORS[:] = errs
    return errs


def _escape(v: Any) -> Any:
    if isinstance(v, str):
        return v.replace("{", "{{").replace("}", "}}")
    if isinstance(v, list):
        return [_escape(x) for x in v]
    if isinstance(v, dict):
        return {k: _escape(x) for k, x in v.items()}
    return v


def device_as_template(dev: DeviceConfig, name: str, title: Optional[str] = None,
                       description: Optional[str] = None) -> str:
    """YAML text of a user template built from a device's expanded points (literal values, no params).

    A device byte / word order other than ``big`` is copied onto the points that do not set their own.
    """
    if not NAME_RE.match(name):
        raise ValueError(f"invalid template name {name!r} (letters, digits, _ . -)")
    pts = []
    for p in expand_device(dev):
        d = p.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        d = {"name": p.name, **{k: v for k, v in d.items() if k != "name"}}
        for k in ("byte_order", "word_order"):
            if getattr(dev, k) != "big" and k not in d:
                d[k] = getattr(dev, k)
        if "labels" in d:
            d["labels"] = {int(k): v for k, v in d["labels"].items()}
        pts.append({k: (v if k == "name" else _escape(v)) for k, v in d.items()})
    doc = {"name": name, "title": title or name,
           "description": description or f"Saved from device {dev.name}", "params": {}, "points": pts}
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)

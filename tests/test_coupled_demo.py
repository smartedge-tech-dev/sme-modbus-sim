"""configs/demo-coupled-site.yaml: PV + load + battery + grid meter coupled through expressions."""
from datetime import datetime
from pathlib import Path

import pytest

from sme_modbus_sim.config import load_file
from sme_modbus_sim.engine import Engine

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "demo-coupled-site.yaml"


def _engine(start_hour: int) -> Engine:
    e = Engine(load_file(CONFIG))
    e.sim_epoch = datetime(2026, 6, 1, start_hour, 0).timestamp()  # deterministic local "hour"
    return e


def _v(e: Engine, dev: str, point: str) -> float:
    return e.devices[dev].by_name[point].value


def _write_pcs(e: Engine, setpoint_kw: float, run: bool) -> None:
    store = e.devices["PCS-1"].store  # same path as a Modbus FC 06 / FC 05 write
    store.write("holding", 0, [int(round(setpoint_kw * 10)) & 0xFFFF])
    store.write("coil", 0, [int(run)])


def _run(e: Engine, seconds: int) -> None:
    for _ in range(seconds):
        e.step(1.0)


def _assert_balance(e: Engine) -> None:
    expected = _v(e, "Load-1", "active_power") - _v(e, "PV-1", "p_ac") - _v(e, "PCS-1", "p_actual")
    assert _v(e, "Meter-1", "active_power") == pytest.approx(expected, abs=0.01)


def test_config_loads_without_behavior_errors():
    e = _engine(12)
    _run(e, 5)
    errors = [(d.name, p.name, p.error) for d in e.devices.values() for p in d.points if p.error]
    assert errors == []


def test_discharge_lowers_soc_and_meter_balances():
    e = _engine(12)
    soc0 = _v(e, "BMS-1", "soc")
    _write_pcs(e, 20, True)
    _run(e, 600)
    assert _v(e, "PCS-1", "state") == 2
    assert _v(e, "PCS-1", "p_actual") == pytest.approx(20)
    assert _v(e, "PV-1", "p_ac") > 0  # noon: daylight
    # 20 kW for ~600 s on 100 kWh -> about -3.3 %
    assert soc0 - 3.6 < _v(e, "BMS-1", "soc") < soc0 - 3.0
    _assert_balance(e)
    assert _v(e, "Meter-1", "export_energy") > 0  # PV + battery exceed the midday load


def test_charge_raises_soc_and_night_has_no_pv():
    e = _engine(22)
    soc0 = _v(e, "BMS-1", "soc")
    _write_pcs(e, -30, True)
    _run(e, 300)
    assert _v(e, "PV-1", "p_ac") == 0
    assert _v(e, "PCS-1", "p_actual") == pytest.approx(-30)
    assert _v(e, "BMS-1", "soc") > soc0 + 2.0  # 30 kW x 300 s on 100 kWh -> about +2.4 %
    _assert_balance(e)
    assert _v(e, "Meter-1", "active_power") > 30  # load + charging, all imported
    assert _v(e, "Meter-1", "import_energy") > 0


def test_stop_command_holds_soc():
    e = _engine(12)
    _write_pcs(e, 20, False)  # setpoint without run_command: PCS stays stopped
    soc0 = _v(e, "BMS-1", "soc")
    _run(e, 120)
    assert _v(e, "PCS-1", "p_actual") == 0
    assert _v(e, "BMS-1", "soc") == pytest.approx(soc0)
    _assert_balance(e)

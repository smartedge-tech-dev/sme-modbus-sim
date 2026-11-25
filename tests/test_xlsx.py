"""Excel point table: blank template, export -> import round trip, Modicon addresses, error mapping."""

import io
from pathlib import Path

import pytest
from openpyxl import load_workbook

from sme_modbus_sim.config import load_file, parse_text
from sme_modbus_sim.templates import expand_device
from sme_modbus_sim.xlsx import SHEETS, blank_template, export_xlsx, import_xlsx, parse_address, to_yaml

DEMO = Path(__file__).resolve().parents[1] / "configs" / "demo-site.yaml"

RICH = """
simulation: {tick_seconds: 0.5}
listeners:
  - {id: tcp1, type: tcp, port: 15020}
  - {id: gw, type: rtu_over_tcp, port: 15021, unit_id_mode: any, unknown_unit: exception, default_unit: 2}
devices:
  - name: A
    description: rich device
    unit_id: 1
    listeners: [tcp1]
    byte_order: little
    undefined_address: zero
    identification: {vendor_name: SME, product_code: "X-1"}
    points:
      - {name: s, table: holding, address: 10, datatype: string, count: 4, value: "AB{C}", access: r}
      - {name: e, table: input, address: 0, datatype: enum, labels: {0: "OFF", 1: "ON"}, value: 1}
      - {name: f, table: holding, address: 0, datatype: float32, word_order: little, scale: 0.5, offset: -1,
         min: -1, max: 9.5, units: kW, behavior: {type: sine, min: 0, max: 5, period: 60}}
      - {name: c, table: coil, address: 3, value: true}
      - {name: b, table: input, address: 1, datatype: bit, bit: 3}
      - {name: n, table: holding, datatype: int64, value: -5}
  - name: P
    unit_id: 2
    template: battery_pcs
    params: {rated_kw: 20, bms_device: ""}
    removed: [alarm_grid]
    points:
      - {name: p_setpoint, value: 5}
      - {name: extra_reg, table: holding, address: 50}
"""


def _wb(devices, points, listeners=None) -> bytes:
    wb = load_workbook(io.BytesIO(blank_template()))
    for title, rows in (("Devices", devices), ("Points", points), ("Listeners", listeners or [])):
        cols = list(SHEETS[title])
        for r in rows:
            wb[title].append([r.get(c) for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_blank_template_layout():
    wb = load_workbook(io.BytesIO(blank_template()))
    assert wb.sheetnames == ["Listeners", "Devices", "Points"]
    for title, cols in SHEETS.items():
        assert [c.value for c in wb[title][1]] == list(cols)
        assert str(wb[title].cell(2, 1).value).startswith("#")
    base = load_file(DEMO)
    cfg, errs = import_xlsx(blank_template(), base)
    assert cfg is not None and cfg.devices == [] and [l.id for l in cfg.listeners] == [l.id for l in base.listeners], errs


@pytest.mark.parametrize("src", ["demo", "rich"])
def test_round_trip(src):
    cfg = load_file(DEMO) if src == "demo" else parse_text(RICH)
    back, warnings = import_xlsx(export_xlsx(cfg), cfg)
    assert back is not None, warnings
    assert warnings == []
    assert back.model_dump() == cfg.model_dump()
    for a, b in zip(cfg.devices, back.devices):
        assert expand_device(a) == expand_device(b)
    # the YAML written after an import re-parses to the same config (template overrides stay minimal)
    assert parse_text(to_yaml(back)).model_dump() == cfg.model_dump()


def test_parse_address():
    assert parse_address(0) == (0, None)
    assert parse_address(40001) == (40001, None)  # a number is the 0-based address
    assert parse_address(40001.0) == (40001, None)
    assert parse_address("123") == (123, None)
    assert parse_address("40001") == (0, "holding")
    assert parse_address("30010") == (9, "input")
    assert parse_address("10001") == (0, "discrete")
    assert parse_address("00001") == (0, "coil")
    assert parse_address("400001") == (0, "holding")
    assert parse_address(465536) == (65535, "holding")  # 6-digit number above 65535: Modicon
    for bad in ("40000", "abc", -1, 70000, "465537", True, "1.5"):
        with pytest.raises(ValueError):
            parse_address(bad)


def test_import_modicon_extra_and_listeners():
    data = _wb([{"name": "M", "unit_id": 3, "listeners": "t", "extra": "{removed: []}"}],
               [{"device": "M", "address": "40001", "name": "a", "datatype": "INT16"},
                {"device": "M", "address": 300002, "name": "b", "extra": "{description: from extra}"},
                {"device": "M", "table": "coil", "address": 5, "name": "c", "value": "TRUE"},
                {"device": "M", "table": "Holding", "address": "'40001", "name": "d"}][:3],
               [{"id": "t", "port": 15099, "extra": "{host: 127.0.0.1}"}])
    cfg, warnings = import_xlsx(data)
    assert cfg is not None, warnings
    pts = {p.name: p for p in cfg.devices[0].points}
    assert (pts["a"].table, pts["a"].address, pts["a"].datatype) == ("holding", 0, "int16")
    assert (pts["b"].table, pts["b"].address, pts["b"].description) == ("input", 1, "from extra")
    assert (pts["c"].table, pts["c"].address) == ("coil", 5)
    assert cfg.listeners[0].host == "127.0.0.1" and cfg.listeners[0].port == 15099


def test_import_row_errors_mapped():
    data = _wb([{"name": "M", "unit_id": 1}, {"name": "N", "unit_id": 2, "params": "[1"},
                {"name": "N", "unit_id": 4}, {"name": "Q", "unit_id": "x"}],
               [{"device": "X", "name": "a", "table": "holding"},
                {"device": "M", "name": "b", "table": "holding", "address": "30001"},
                {"device": "M", "name": "c", "table": "input", "datatype": "bool"},
                {"device": "M", "name": "d", "table": "input", "address": "abc"},
                {"device": "M", "name": "d2", "table": "input", "behavior": "{min: 1}"},
                {"device": "M", "table": "input"}],
               [{"id": "t", "port": 0}])
    cfg, errs = import_xlsx(data)
    assert cfg is None
    text = "\n".join(errs)
    for needle in ("Listeners row 3, column 'port'",
                   "Devices row 4, column 'params': invalid YAML",
                   "Devices row 5, column 'name': duplicate device 'N'",
                   "Devices row 6, column 'unit_id': not an integer",
                   "Points row 3, column 'device': device 'X' is not in the Devices sheet",
                   "Points row 4, column 'address': Modicon address 30001 is a input address",
                   "Points row 5: ",
                   "Points row 6, column 'address': not an address",
                   "Points row 7, column 'behavior'",
                   "Points row 8, column 'name': required"):
        assert needle in text, f"{needle!r} not in:\n{text}"


def test_import_cross_row_errors_mapped():
    data = _wb([{"name": "M", "unit_id": 1}, {"name": "Z", "unit_id": 300},
                {"name": "T", "unit_id": 2, "template": "nope"}],
               [{"device": "M", "name": "a", "table": "holding", "address": 0, "datatype": "int32"},
                {"device": "M", "name": "b", "table": "holding", "address": 1}])
    cfg, errs = import_xlsx(data)
    assert cfg is None
    text = "\n".join(errs)
    assert "Points row 4: M/b: holding 1..1 overlaps 'a'" in text
    assert "Devices row 4, column 'unit_id'" in text
    assert "Devices row 5: T: unknown template 'nope'" in text

    data = _wb([{"name": "M", "unit_id": 1, "listeners": "tcp9"}], [])
    cfg, errs = import_xlsx(data)
    assert cfg is None and errs[0].startswith("Devices row 3: ") and "tcp9" in errs[0]


def test_import_not_a_workbook():
    assert import_xlsx(b"junk")[0] is None
    wb = load_workbook(io.BytesIO(blank_template()))
    del wb["Points"]
    buf = io.BytesIO()
    wb.save(buf)
    cfg, errs = import_xlsx(buf.getvalue())
    assert cfg is None and errs == ["sheet 'Points' is missing"]

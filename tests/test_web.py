from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sme_modbus_sim import __version__
from sme_modbus_sim.__main__ import main
from sme_modbus_sim.config import parse_text
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.web import create_app

CONFIG = """
simulation: {tick_seconds: 0.1, time_scale: 1}
listeners:
  - {id: tcp1, type: tcp, host: 127.0.0.1, port: 15020}
devices:
  - name: D
    unit_id: 1
    points:
      - {name: sp, table: holding, address: 0, datatype: int16, scale: 0.1, min: -50, max: 50}
      - {name: ro, table: input, address: 0, value: 7}
      - {name: elapsed, table: input, address: 1, datatype: float32, behavior: {type: expr, expr: t}}
"""


@pytest.fixture()
def client(tmp_path):
    path = tmp_path / "site.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    eng = Engine(parse_text(CONFIG), tmp_path, config_text=CONFIG,
                 listener_overrides={"tcp1": {"host": "127.0.0.1", "port": 0},
                                     "tcp2": {"host": "127.0.0.1", "port": 0}})
    with TestClient(create_app(eng, path)) as c:
        c.engine, c.path = eng, path
        yield c


def test_read_routes(client):
    assert client.get("/api/version").json() == {"name": "SME ModbusSim", "version": __version__}
    st = client.get("/api/state").json()
    assert st["devices"][0]["name"] == "D" and st["listeners"][0]["bound_port"] > 0
    assert {t["name"] for t in client.get("/api/templates").json()} >= {"battery_pcs", "custom"}
    h = client.get("/api/history/D/ro").json()
    assert h["point"] == "ro" and isinstance(h["history"], list)
    assert client.get("/api/history/D/nope").status_code == 404
    assert "tcp1" in client.get("/api/stats").json()
    assert client.get("/api/trace?limit=5&unit=1&fc=3").json() == []
    assert client.get("/").status_code == 200


def test_write_and_simulation(client):
    r = client.post("/api/devices/D/points/sp/write", json={"value": 12.3})
    assert r.status_code == 200 and r.json()["value"] == pytest.approx(12.3)
    assert client.engine.devices["D"].store.get("holding", 0) == [123]
    assert client.post("/api/devices/D/points/sp/write", json={"value": 99}).status_code == 400
    assert client.post("/api/devices/D/points/x/write", json={"value": 1}).status_code == 404
    assert client.post("/api/devices/D/points/ro/write", json={"value": 3}).json()["value"] == 3
    r = client.post("/api/simulation", json={"running": False, "time_scale": 4})
    assert r.json() == {"running": False, "time_scale": 4}
    assert client.post("/api/simulation", json={"time_scale": -1}).status_code == 400


def test_config_get_put_save(client):
    assert client.get("/api/config").text == CONFIG
    bad = client.put("/api/config", content="devices: [{name: X}]")
    assert bad.status_code == 400 and "unit_id" in bad.json()["detail"]
    new = CONFIG.replace("value: 7", "value: 8") + """
  - name: E
    unit_id: 2
    template: env_sensor
""".replace("listeners:\n", "")
    new = new.replace("  - {id: tcp1, type: tcp, host: 127.0.0.1, port: 15020}",
                      "  - {id: tcp1, type: tcp, host: 127.0.0.1, port: 15020}\n"
                      "  - {id: tcp2, type: rtu_over_tcp, host: 127.0.0.1, port: 15021}")
    r = client.put("/api/config", content=new)
    assert r.status_code == 200, r.text
    st = client.get("/api/state").json()
    assert [d["name"] for d in st["devices"]] == ["D", "E"] and len(st["listeners"]) == 2
    assert all(l["bound_port"] > 0 for l in st["listeners"])
    assert client.get("/api/config").text == new
    assert client.post("/api/config/save").json()["ok"]
    assert client.path.read_text(encoding="utf-8") == new


def test_websocket(client):
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["data"]["devices"][0]["name"] == "D"
        client.post("/api/devices/D/points/sp/write", json={"value": 1})
        seen = set()
        for _ in range(20):
            msg = ws.receive_json()
            seen.add(msg["type"])
            if "values" in seen and "log" in seen:
                break
        assert {"values", "log"} <= seen


def test_cli_validate_and_templates(tmp_path, capsys):
    demo = Path(__file__).resolve().parent.parent / "configs" / "demo-site.yaml"
    assert main(["validate", str(demo)]) == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text("devices: [{name: X, unit_id: 0}]", encoding="utf-8")
    assert main(["validate", str(bad)]) == 1
    assert main(["templates"]) == 0
    assert "battery_pcs" in capsys.readouterr().out
    with pytest.raises(SystemExit) as ei:
        main(["--version"])
    assert ei.value.code == 0


# ---------------------------------------------------------------- Excel / user templates

import io  # noqa: E402

from openpyxl import load_workbook  # noqa: E402

from sme_modbus_sim import templates as tpl  # noqa: E402
from sme_modbus_sim.web import XLSX_TYPE  # noqa: E402
from sme_modbus_sim.xlsx import SHEETS  # noqa: E402


@pytest.fixture(autouse=True)
def tpl_dir(tmp_path, monkeypatch):
    d = tmp_path / "user-templates"
    monkeypatch.setenv(tpl.ENV_TEMPLATES_DIR, str(d))
    monkeypatch.setattr(tpl, "_user_dir", None)
    yield d
    tpl.clear_user_templates()


def _set(wb, sheet, key_col, key, col, value):
    cols = list(SHEETS[sheet])
    for row in wb[sheet].iter_rows(min_row=3):
        if row[cols.index(key_col)].value == key:
            row[cols.index(col)].value = value
            return
    raise AssertionError(key)


def _bytes(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_routes(client):
    r = client.get("/api/config/xlsx")
    assert r.status_code == 200 and r.headers["content-type"].startswith(XLSX_TYPE)
    assert "attachment" in r.headers["content-disposition"]
    blank = client.get("/api/config/xlsx-template")
    assert blank.status_code == 200 and load_workbook(io.BytesIO(blank.content)).sheetnames[0] == "Listeners"
    wb = load_workbook(io.BytesIO(r.content))
    _set(wb, "Points", "name", "ro", "value", 9)
    r2 = client.post("/api/config/import", files={"file": ("p.xlsx", _bytes(wb), XLSX_TYPE)})
    assert r2.status_code == 200, r2.text
    assert r2.json()["devices"] == 1
    cfg = parse_text(client.get("/api/config").text)
    assert {p.name: p.value for p in cfg.devices[0].points}["ro"] == 9
    assert cfg.simulation.tick_seconds == 0.1  # simulation section kept from the running config
    assert client.engine.devices["D"].store.get("input", 0) == [9]

    _set(wb, "Devices", "name", "D", "unit_id", "x")
    bad = client.post("/api/config/import", files={"file": ("p.xlsx", _bytes(wb), XLSX_TYPE)})
    assert bad.status_code == 400
    detail = bad.json()["detail"]
    assert "1 error" in detail["message"] and detail["errors"][0].startswith("Devices row 3, column 'unit_id'")
    junk = client.post("/api/config/import", files={"file": ("p.xlsx", b"junk", XLSX_TYPE)})
    assert junk.status_code == 400 and "not a readable" in junk.json()["detail"]["errors"][0]


def test_template_routes(client, tpl_dir):
    r = client.post("/api/templates/from-device/D", json={"name": "dev_d"})
    assert r.status_code == 200, r.text
    assert Path(r.json()["path"]) == tpl_dir / "dev_d.yaml"
    assert client.post("/api/templates/from-device/D", json={"name": "dev_d"}).status_code == 409
    assert client.post("/api/templates/from-device/D", json={"name": "dev_d", "overwrite": True}).status_code == 200
    assert client.post("/api/templates/from-device/nope", json={"name": "x"}).status_code == 404
    assert client.post("/api/templates/from-device/D", json={"name": "bad name"}).status_code == 400
    cat = {t["name"]: t for t in client.get("/api/templates").json()}
    assert cat["dev_d"]["source"] == "user" and cat["battery_pcs"]["source"] == "builtin"
    (tpl_dir / "broken.yaml").write_text("points: [", encoding="utf-8")
    r = client.post("/api/templates/reload").json()
    assert r["loaded"] == ["dev_d"] and len(r["errors"]) == 1 and r["dir"] == str(tpl_dir)
    new = CONFIG + "  - {name: E, unit_id: 2, template: dev_d}\n"
    assert client.put("/api/config", content=new).status_code == 200
    names = [p["name"] for p in client.get("/api/state").json()["devices"][1]["points"]]
    assert names == ["sp", "ro", "elapsed"]


def test_cli_templates_dir(tmp_path, capsys):
    d = tmp_path / "tpls"
    d.mkdir()
    (d / "mine.yaml").write_text("title: Mine\npoints: [{name: a, table: holding}]\n", encoding="utf-8")
    (d / "bad.yaml").write_text("points: [", encoding="utf-8")
    assert main(["templates", "--templates-dir", str(d)]) == 0
    out, err = capsys.readouterr()
    assert "mine" in out and "[user:" in out and "bad.yaml" in err
    cfg = tmp_path / "c.yaml"
    cfg.write_text("devices: [{name: X, unit_id: 1, template: mine}]", encoding="utf-8")
    assert main(["validate", str(cfg), "--templates-dir", str(d)]) == 0


# ---- device management (add / duplicate / delete / per-device YAML) and the stats push


def _names(client):
    return [d["name"] for d in client.get("/api/state").json()["devices"]]


def test_device_crud(client):
    r = client.post("/api/devices", json={"name": "M", "unit_id": 5, "template": "power_meter"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "name": "M", "unit_id": 5}
    assert _names(client) == ["D", "M"]
    assert "template: power_meter" in client.get("/api/config").text
    assert client.post("/api/devices", json={"name": "M", "unit_id": 6}).status_code == 409
    r = client.post("/api/devices", json={"name": "X", "unit_id": 0})
    assert r.status_code == 400 and "unit_id" in r.json()["detail"]
    r = client.post("/api/devices", json={"name": "X", "unit_id": 5, "template": "power_meter"})
    assert r.status_code == 400 and "unit_id 5" in r.json()["detail"]
    r = client.post("/api/devices", json={"name": "X", "unit_id": 7, "points": [{"name": "pv", "table": "holding"}]})
    assert r.status_code == 400 and "reserved" in r.json()["detail"]
    assert client.post("/api/devices", content=b"[").status_code == 400
    yml = client.get("/api/devices/M/config").text
    assert yml.startswith("name: M") and "points" not in yml  # template device: overrides only
    r = client.put("/api/devices/M/config", content=yml.replace("unit_id: 5", "unit_id: 9").replace("name: M", "name: M2"))
    assert r.status_code == 200 and r.json()["name"] == "M2"
    assert _names(client) == ["D", "M2"]
    assert client.put("/api/devices/M2/config", content="name: D\nunit_id: 9\n").status_code == 409
    assert client.put("/api/devices/M2/config", content="name: [").status_code == 400
    assert client.put("/api/devices/M2/config", content="- a\n").status_code == 400
    assert client.get("/api/devices/M/config").status_code == 404
    assert client.delete("/api/devices/M2").status_code == 200
    assert client.delete("/api/devices/M2").status_code == 404
    assert _names(client) == ["D"]
    assert "M2" not in client.get("/api/config").text


def test_device_duplicate(client):
    r = client.post("/api/devices/D/duplicate")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "name": "D_copy", "unit_id": 2}
    r = client.post("/api/devices/D/duplicate", json={})
    assert r.json() == {"ok": True, "name": "D_copy2", "unit_id": 3}
    assert client.post("/api/devices/D/duplicate", json={"name": "D_copy"}).status_code == 409
    assert client.post("/api/devices/D/duplicate", json={"name": "Z", "unit_id": 2}).status_code == 400
    assert client.post("/api/devices/nope/duplicate").status_code == 404
    devs = {d["name"]: d for d in client.get("/api/state").json()["devices"]}
    assert [p["name"] for p in devs["D_copy"]["points"]] == [p["name"] for p in devs["D"]["points"]]


def test_next_free_unit():
    from sme_modbus_sim.web import next_free_unit

    cfg = parse_text("listeners: [{id: a, port: 1502}, {id: b, port: 1503}]\n"
                     "devices: [{name: A, unit_id: 1, listeners: [a]}, {name: B, unit_id: 2},"
                     " {name: C, unit_id: 3, listeners: [b]}]")
    assert next_free_unit(cfg, ["a"]) == 3
    assert next_free_unit(cfg, ["b"]) == 1
    assert next_free_unit(cfg, ["a", "b"]) == 4
    full = parse_text("devices: [" + ", ".join(f"{{name: d{u}, unit_id: {u}}}" for u in range(1, 248)) + "]")
    assert next_free_unit(full, ["tcp1"]) is None


def test_websocket_stats_push(client):
    client.engine.stats_interval = 0.2
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        for _ in range(500):
            msg = ws.receive_json()
            if msg["type"] == "stats":
                break
        assert msg["type"] == "stats" and "tcp1" in msg["stats"]

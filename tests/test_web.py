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
      - {name: t, table: input, address: 1, datatype: float32, behavior: {type: expr, expr: t}}
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

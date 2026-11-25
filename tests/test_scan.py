"""Read-only client, scan heuristics, record CSV, REST jobs and CLI for scan / record (no real hardware)."""
import asyncio
import io
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sme_sim_common.behaviors import ReplayTable

from sme_modbus_sim import scan as sc
from sme_modbus_sim import templates as tpl
from sme_modbus_sim.__main__ import main
from sme_modbus_sim.config import PointConfig, parse_text
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.protocol.client import ModbusClient, ResponseTimeout, rtu_response_length
from sme_modbus_sim.protocol.datatypes import encode
from sme_modbus_sim.protocol.errors import ModbusError
from sme_modbus_sim.protocol.server import ModbusTcpServer
from sme_modbus_sim.protocol.store import UnitStore
from sme_modbus_sim.web import create_app


# ---------------------------------------------------------------- fixtures

def make_store() -> UnitStore:
    """Holding 0-9 / 20-23 / 30-33 with holes; coils 0-3; identification."""
    s = UnitStore(1)
    s.define("holding", 0, 10)
    s.define("holding", 20, 4)
    s.define("holding", 30, 4)
    s.define("coil", 0, 4)
    s.set("holding", 0, [3, 7, 11, 0, 0, encode(-5, "int16")[0], 100, 200, 300, 400])
    s.set("holding", 20, encode(230.5, "float32") + encode(49.98, "float32", word_order="little"))
    s.set("holding", 30, encode("SME-SIM1", "string", count=4))
    s.set("coil", 0, [1, 0, 1, 1])
    s.identification = {0: "ACME", 1: "PX-1", 2: "1.0"}
    return s


async def start_server(framer="tcp", store=None):
    store = store or make_store()
    units = {1: store}
    srv = ModbusTcpServer("127.0.0.1", 0, framer=framer, resolver=units.get, all_units=lambda: units.values())
    await srv.start()
    return srv


class ThreadServer:
    """A server on its own loop in a thread, for the CLI (which calls asyncio.run itself)."""

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.srv = self.loop.run_until_complete(start_server())
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert ready.wait(5)
        return self.srv.bound_port

    def __exit__(self, *exc):
        fut = asyncio.run_coroutine_threadsafe(self.srv.stop(), self.loop)
        fut.result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


# ---------------------------------------------------------------- client

@pytest.mark.parametrize("framer", ["tcp", "rtu_over_tcp"])
def test_client_reads(framer):
    async def run():
        srv = await start_server(framer)
        try:
            async with ModbusClient(framer, "127.0.0.1", srv.bound_port, rate=1000, timeout=1) as c:
                assert await c.read_holding_registers(1, 0, 3) == [3, 7, 11]
                assert await c.read_coils(1, 0, 4) == [1, 0, 1, 1]
                with pytest.raises(ModbusError) as ei:
                    await c.read_holding_registers(1, 10, 2)
                assert ei.value.exception_code == 2
                ident = await c.read_device_identification(1, 1)
                assert ident == {0: "ACME", 1: "PX-1", 2: "1.0"}
                assert c.requests == 4
                with pytest.raises(ResponseTimeout):  # unknown unit: the server stays silent
                    c.timeout = 0.2
                    await c.read_holding_registers(9, 0, 1)
        finally:
            await srv.stop()

    asyncio.run(run())


def test_client_refuses_write_function_codes():
    async def run():
        c = ModbusClient("tcp", "127.0.0.1", 1)
        for pdu in (b"\x06\x00\x00\x00\x01", b"\x05\x00\x00\xff\x00", b"\x10\x00\x00\x00\x01\x02\x00\x01",
                    b"\x0f\x00\x00\x00\x01\x01\x01", b"\x16\x00\x00\xff\xff\x00\x00", b"\x17" + bytes(9),
                    b"\x2b\x0d\x00\x00"):
            with pytest.raises(PermissionError):
                await c._request(1, pdu)
        assert c.requests == 0
        assert not any(callable(getattr(c, n, None)) for n in dir(c) if n.startswith("write"))

    asyncio.run(run())


def test_client_rate_limit():
    async def run():
        srv = await start_server()
        try:
            async with ModbusClient("tcp", "127.0.0.1", srv.bound_port, rate=20) as c:
                t = time.monotonic()
                for _ in range(5):
                    await c.read_holding_registers(1, 0, 1)
                assert time.monotonic() - t >= 0.18
        finally:
            await srv.stop()

    asyncio.run(run())


def test_rtu_response_length():
    assert rtu_response_length(b"\x01") is None
    assert rtu_response_length(b"\x01\x83\x02") == 5
    assert rtu_response_length(b"\x01\x03\x04") == 9
    assert rtu_response_length(b"\x01\x2b\x0e\x01\x81\x00\x00\x01\x00\x03ABC") == 15


# ---------------------------------------------------------------- heuristics

def test_guess_float32_abcd_and_cdab():
    w = [encode(v, "float32") for v in (230.1, 230.4, 230.2)]
    g = sc.guess_float32([x[0] for x in w], [x[1] for x in w])
    assert g["datatype"] == "float32" and g["word_order"] == "big" and g["confidence"] >= 0.8
    w = [encode(v, "float32", word_order="little") for v in (49.98, 50.01, 50.0)]
    g = sc.guess_float32([x[0] for x in w], [x[1] for x in w])
    assert g["word_order"] == "little" and "CDAB" in g["reason"]
    assert sc.guess_float32([0, 0], [0, 0]) is None
    assert sc.guess_float32([100, 101], [200, 201]) is None  # denormal -> two small ints
    hi = [encode(v, "float32")[0] for v in (10.0, 12.0)]
    assert sc.guess_float32([7, 7], hi) is None  # uint16 7 followed by a float's high word, not CDAB
    assert not sc.float_plausible(float("nan")) and not sc.float_plausible(1e20) and sc.float_plausible(0.0)


def test_guess_int32_int16_string_bool():
    g = sc.guess_int32([0xFFFF, 0xFFFF, 0xFFFF], [0xFF00, 0xFE00, 0xFD00])
    assert g["datatype"] == "int32" and g["word_order"] == "big"
    assert sc.guess_int32([0, 0, 0], [5, 6, 7])["datatype"] == "uint32"
    assert sc.guess_int32([0, 0, 0], [5, 5, 5]) is None
    assert sc.guess_int16([0xFFFB, 0xFFFA])["datatype"] == "int16"
    assert sc.guess_int16([0x8001]) is None
    words = [[w] * 3 for w in encode("ABCDEF", "string", count=4)]
    g = sc.guess_string(words)
    assert g["datatype"] == "string" and g["count"] == 3 and "ABCDEF" in g["reason"]
    assert sc.guess_string([[0x4142], [0x0102]]) is None
    assert sc.guess_bool([1, 1, 1])["datatype"] == "bool"


def test_suggest_consumes_multi_register_points():
    samples = {0: [3], 1: [7], 5: [0xFFFB],
               20: [encode(230.5, "float32")[0]], 21: [encode(230.5, "float32")[1]]}
    out = {s["address"]: s for s in sc.suggest("holding", samples)}
    assert sorted(out) == [0, 1, 5, 20]
    assert out[20]["datatype"] == "float32" and out[20]["value"] == 230.5
    assert out[5]["datatype"] == "int16" and out[5]["value"] == -5
    assert out[0]["datatype"] == "uint16" and out[0]["name"] == "hr_0"


def test_parse_range():
    assert sc.parse_range("holding:0-99").as_dict() == {"table": "holding", "start": 0, "count": 100}
    assert sc.parse_range({"table": "coil", "start": 5, "count": 2}).count == 2
    for bad in ("holding:9-1", "x:1", "holding:65535-65536"):
        with pytest.raises(ValueError):
            sc.parse_range(bad)
    with pytest.raises(ValueError):
        sc.parse_ranges([])


# ---------------------------------------------------------------- scan / template / record

def test_scan_discovers_layout_with_holes():
    async def run():
        srv = await start_server("rtu_over_tcp")
        try:
            async with ModbusClient("rtu_over_tcp", "127.0.0.1", srv.bound_port, rate=1000) as c:
                return await sc.scan(c, 1, sc.parse_ranges(["holding:0-39", "coil:0-7"]), samples=2,
                                     sample_interval=0)
        finally:
            await srv.stop()

    res = asyncio.run(run())
    hr = [r["address"] for r in res["readable"] if r["table"] == "holding"]
    assert hr == [*range(10), *range(20, 24), *range(30, 34)]
    assert [r["address"] for r in res["readable"] if r["table"] == "coil"] == [0, 1, 2, 3]
    assert all(len(r["samples"]) == 2 for r in res["readable"])
    sug = {(s["table"], s["address"]): s for s in res["suggestions"]}
    assert sug[("holding", 20)]["datatype"] == "float32" and sug[("holding", 20)]["value"] == 230.5
    assert sug[("holding", 22)]["word_order"] == "little" and sug[("holding", 22)]["value"] == 49.98
    assert sug[("holding", 30)]["datatype"] == "string" and sug[("holding", 30)]["value"] == "SME-SIM1"
    assert sug[("holding", 5)]["datatype"] == "int16"
    assert sug[("coil", 0)]["datatype"] == "bool"
    assert res["identification"]["vendor_name"] == "ACME"

    tdoc = sc.to_template(res, [{"table": "holding", "address": 20, "name": "voltage", "units": "V"},
                               {"table": "holding", "address": 9, "keep": False}], name="px1")
    names = [p["name"] for p in tdoc["points"]]
    assert "voltage" in names and "hr_9" not in names and "ACME" in tdoc["description"]
    text = sc.template_yaml(tdoc, res)
    assert "# guess float32" in text
    loaded = yaml.safe_load(text)
    assert loaded["name"] == "px1" and len(loaded["points"]) == len(tdoc["points"])
    with pytest.raises(ValueError):  # float32 at 21 overlaps the one at 20
        sc.to_points(res, [{"table": "holding", "address": 21, "datatype": "float32"}])


def test_scan_timeouts_abort():
    async def run():
        srv = await start_server()
        try:
            async with ModbusClient("tcp", "127.0.0.1", srv.bound_port, rate=1000, timeout=0.1) as c:
                await sc.scan(c, 9, sc.parse_ranges(["holding:0-9"]), samples=1, max_timeouts=2)
        finally:
            await srv.stop()

    with pytest.raises(ResponseTimeout):
        asyncio.run(run())


def test_record_csv_readable_by_replay(tmp_path):
    points = [PointConfig.model_validate(p) for p in (
        {"name": "a", "table": "holding", "address": 0},
        {"name": "v", "table": "holding", "address": 20, "datatype": "float32"},
        {"name": "f", "table": "holding", "address": 22, "datatype": "float32", "word_order": "little"},
        {"name": "neg", "table": "holding", "address": 5, "datatype": "int16", "scale": 0.1},
        {"name": "c", "table": "coil", "address": 3},
        {"name": "gone", "table": "holding", "address": 15},  # unreadable
    )]
    out = tmp_path / "rec.csv"

    async def run():
        srv = await start_server()
        try:
            async with ModbusClient("tcp", "127.0.0.1", srv.bound_port, rate=1000) as c:
                return await sc.record(c, 1, points, 0.05, 0.1, out)
        finally:
            await srv.stop()

    res = asyncio.run(run())
    assert res["rows"] == 3 and res["failed_reads"] == 3 and res["warnings"]
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "timestamp,a,v,f,neg,c,gone"
    assert lines[1].split(",")[1:] == ["3", "230.5", "49.98", "-0.5", "1", ""]
    out.write_text("\n".join(lines[0:1] + [l.rsplit(",", 1)[0] + ",0" if i == 0 else l
                                          for i, l in enumerate(lines[1:])]) + "\n", encoding="utf-8")
    t = ReplayTable(out)
    assert t.column("v") == [230.5] * 3 and t.column("gone") == [0.0] * 3  # empty cells hold the previous value
    assert 0.05 <= t.duration < 1


# ---------------------------------------------------------------- REST

CONFIG = """
simulation: {tick_seconds: 0.1, time_scale: 1}
listeners:
  - {id: tcp1, type: tcp, host: 127.0.0.1, port: 15020}
devices:
  - name: D
    unit_id: 1
    identification: {vendor_name: ACME, product_code: PX-2}
    points:
      - {name: sp, table: holding, address: 0, datatype: int16, value: -3}
      - {name: ro, table: input, address: 0, value: 7}
      - {name: elapsed, table: input, address: 1, datatype: float32, behavior: {type: expr, expr: t + 10}}
"""


@pytest.fixture()
def client(tmp_path):
    path = tmp_path / "site.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    tpl.load_user_templates(tmp_path / "tpl")
    eng = Engine(parse_text(CONFIG), tmp_path, config_text=CONFIG, listener_overrides={"tcp1": {"port": 0}})
    with TestClient(create_app(eng, path)) as c:
        c.engine, c.path = eng, path
        c.port = eng.servers["tcp1"].bound_port
        yield c
    tpl.clear_user_templates()
    tpl.load_user_templates(tpl.default_templates_dir())


def wait_job(c, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = c.get("/api/scan/job").json()
        if job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_rest_scan_flow(client):
    body = {"target": {"type": "tcp", "host": "127.0.0.1", "port": client.port}, "unit": 1,
            "ranges": ["holding:0-3", {"table": "input", "start": 0, "count": 6}],
            "rate": 100, "samples": 3, "sample_interval": 0.3}
    assert client.get("/api/scan/job").status_code == 404
    plan = client.post("/api/scan/plan", json=body).json()
    assert plan["kind"] == "scan" and plan["requests"] == 7 and plan["read_only"].startswith("read-only")
    assert client.post("/api/scan/plan", json={**body, "ranges": []}).status_code == 400
    assert client.post("/api/scan/apply/template", json={"name": "x"}).status_code == 409
    job = client.post("/api/scan/start", json=body).json()
    assert job["state"] == "running" and job["kind"] == "scan"
    job = wait_job(client)
    assert job["state"] == "done", job
    res = job["result"]
    sug = {(s["table"], s["address"]): s for s in res["suggestions"]}
    assert sug[("input", 1)]["datatype"] == "float32"
    assert sug[("holding", 0)]["datatype"] == "int16"
    assert res["identification"]["vendor_name"] == "ACME"

    edits = [{"table": "input", "address": 1, "name": "elapsed"}]
    r = client.post("/api/scan/apply/template", json={"name": "scanned_px", "edits": edits})
    assert r.status_code == 200, r.text
    assert Path(r.json()["path"]).is_file() and tpl.get_template("scanned_px") is not None
    assert client.post("/api/scan/apply/template", json={"name": "scanned_px"}).status_code == 409
    r = client.post("/api/scan/apply/device", json={"name": "copy", "edits": edits})
    assert r.status_code == 200, r.text
    dev = next(d for d in client.engine.config.devices if d.name == "copy")
    assert dev.unit_id == 2 and dev.identification.vendor_name == "ACME"
    assert {p.name for p in dev.points} >= {"elapsed", "hr_0", "ir_0"}

    port = client.engine.servers["tcp1"].bound_port  # the reload may have re-bound the ephemeral port
    rec = {"target": {**body["target"], "port": port}, "unit": 1, "device": "D", "period": 0.1, "duration": 0.2, "rate": 100,
           "file": "rec.csv"}
    plan = client.post("/api/record/plan", json=rec).json()
    assert plan["kind"] == "record" and plan["cycles"] == 3 and plan["file"].endswith("rec.csv")
    assert client.post("/api/record/plan", json={**rec, "file": "../x.csv"}).status_code == 400
    client.post("/api/record/start", json=rec)
    job = wait_job(client)
    assert job["state"] == "done" and job["result"]["rows"] == 3
    csv_text = client.get("/api/scan/job/csv").text
    assert csv_text.splitlines()[0] == "timestamp,sp,ro,elapsed"
    assert ReplayTable(Path(job["result"]["path"])).column("sp") == [-3.0] * 3


def test_rest_single_job_and_cancel(client):
    body = {"target": {"type": "tcp", "host": "127.0.0.1", "port": client.port}, "unit": 1,
            "ranges": ["holding:0-0"], "rate": 100, "samples": 5, "sample_interval": 30}
    events = []
    q = asyncio.Queue()
    client.engine._subs.append(q) if hasattr(client.engine, "_subs") else None
    assert client.post("/api/scan/job/cancel").status_code == 409
    assert client.post("/api/scan/start", json=body).status_code == 200
    r = client.post("/api/scan/start", json=body)
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    assert client.post("/api/record/start", json={**body, "device": "D"}).status_code == 409
    assert client.post("/api/scan/job/cancel").status_code == 200
    job = wait_job(client, 5)
    assert job["state"] == "cancelled"
    while not q.empty():
        events.append(q.get_nowait())
    scan_events = [e for e in events if e.get("type") == "scan"]
    assert scan_events and set(scan_events[-1]["job"]) >= {"id", "kind", "state", "progress", "error"}
    assert "result" not in scan_events[-1]["job"]


def test_rest_bad_target(client):
    r = client.post("/api/scan/plan", json={"target": {"type": "tcp"}, "ranges": ["coil:0"]})
    assert r.status_code == 400 and "host" in r.json()["detail"]
    r = client.post("/api/scan/start", json={"target": {"type": "tcp", "host": "127.0.0.1", "port": 1},
                                             "ranges": ["coil:0"], "timeout": 0.2})
    assert wait_job(client)["state"] == "error"


# ---------------------------------------------------------------- CLI

def test_cli_scan_requires_confirmation(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = main(["scan", "--host", "127.0.0.1", "--port", "1", "--range", "holding:0-9"])
    err = capsys.readouterr().err
    assert rc == 2 and "not interactive" in err and "FC 01/02/03/04 and 2B/0E only" in err
    assert "estimated requests: 4" in err


def test_cli_scan_and_record(tmp_path, capsys):
    out = tmp_path / "px.yaml"
    csv_out = tmp_path / "px.csv"
    with ThreadServer() as port:
        rc = main(["scan", "--host", "127.0.0.1", "--port", str(port), "--range", "holding:0-39", "-y",
                   "--samples", "1", "--rate", "500", "--name", "px", "-o", str(out)])
        assert rc == 0, capsys.readouterr().err
        doc = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert doc["name"] == "px" and any(p["datatype"] == "float32" for p in doc["points"])
        rc = main(["record", "--host", "127.0.0.1", "--port", str(port), "--template", str(out), "-y",
                   "--period", "0.1", "--duration", "0.1", "--rate", "500", "-o", str(csv_out)])
        assert rc == 0, capsys.readouterr().err
    t = ReplayTable(csv_out)
    assert t.column("hr_20") == [230.5, 230.5]
    tpl.clear_user_templates()
    tpl.load_user_templates(tpl.default_templates_dir())


# ---------------------------------------------------------------- reason codes / attach replay

def test_reason_codes_emitted_and_localised():
    from sme_sim_common.testing import iter_i18n_keys
    static = Path(sc.__file__).resolve().parent / "static" / "i18n"
    hi, lo = encode(230.5, "float32"), encode(231.0, "float32")
    guesses = [sc.guess_float32([hi[0], lo[0]], [hi[1], lo[1]]), sc.guess_float32([hi[0]] * 2, [hi[1]] * 2),
               sc.guess_int32([0, 0], [5, 6]), sc.guess_string([[0x4142], [0x4344]]), sc.guess_int16([0xFFFB]),
               sc.guess_bool([0, 1]), sc.guess_bool([1, 1])]
    guesses += sc.suggest("holding", {0: [7, 7], 1: [9, 9]}) + sc.suggest("holding", {0: [3, 3]})
    emitted = {g["reason_code"] for g in guesses if g}
    assert emitted <= set(sc.REASON_CODES) and {"default", "ascii_run", "int16_negative"} <= emitted
    assert all(isinstance(g["reason_params"], dict) and g["reason"] for g in guesses if g)
    for lang in ("en", "ja", "zh"):
        keys = {k.key for k in iter_i18n_keys(static / f"{lang}.js")}
        assert {f"scan.reason.{c}" for c in sc.REASON_CODES} <= keys, lang


def _record(c, **src):
    port = c.engine.servers["tcp1"].bound_port
    body = {"target": {"type": "tcp", "host": "127.0.0.1", "port": port}, "unit": 1, "period": 0.1,
            "duration": 0.2, "rate": 100, "file": "rec.csv", **src}
    r = c.post("/api/record/start", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(c)
    assert job["state"] == "done", job
    return job["result"]


def test_rest_attach_replay(client):
    assert client.post("/api/scan/apply/replay", json={}).status_code == 409
    res = _record(client, device="D")
    path = Path(res["path"])
    assert res["missing_first"] == [] and path.parent == client.path.parent
    lines = path.read_text(encoding="utf-8").splitlines()  # make the played values recognisable
    path.write_text("\n".join([lines[0], *[f"{l.split(',')[0]},42,7,1.5" for l in lines[1:]]]) + "\n",
                    encoding="utf-8")
    dry = client.post("/api/scan/apply/replay", json={"dry_run": True}).json()
    assert dry["device"] == "D" and dry["points"] == ["sp", "ro", "elapsed"] and dry["file"] == "rec.csv"
    assert dry["replaced"] == [{"point": "elapsed", "previous_behavior": {"type": "expr", "expr": "t + 10"}}]
    assert next(d for d in client.engine.config.devices if d.name == "D").points[0].behavior is None
    assert client.post("/api/scan/apply/replay", json={"loop": "yes"}).status_code == 400
    r = client.post("/api/scan/apply/replay", json={"loop": False})
    assert r.status_code == 200, r.text
    dev = next(d for d in client.engine.config.devices if d.name == "D")
    assert dev.points[0].behavior == {"type": "replay", "file": "rec.csv", "column": "sp", "loop": False}
    time.sleep(0.4)
    pts = client.engine.devices["D"].by_name
    assert pts["sp"].value == 42 and pts["elapsed"].value == 1.5
    assert client.post("/api/scan/apply/replay", json={"device": "nope"}).status_code == 400


def test_rest_attach_replay_template_source(client, tmp_path):
    (tmp_path / "tpl").mkdir(exist_ok=True)
    (tmp_path / "tpl" / "rec_tpl.yaml").write_text(
        "name: rec_tpl\ntitle: rec\npoints:\n  - {name: sp, table: holding, address: 0, datatype: int16}\n",
        encoding="utf-8")
    tpl.load_user_templates(tmp_path / "tpl")
    _record(client, template="rec_tpl")
    r = client.post("/api/scan/apply/replay", json={})
    assert r.status_code == 400 and "rec_tpl" in r.json()["detail"]
    r = client.post("/api/scan/apply/replay", json={"device": "D"})
    assert r.status_code == 200, r.text
    assert r.json()["points"] == ["sp"] and r.json()["replaced"] == []
    # a column the target device does not have
    _record(client, points=[{"name": "other", "table": "holding", "address": 0, "datatype": "int16"}])
    r = client.post("/api/scan/apply/replay", json={"device": "D"})
    assert r.status_code == 400 and "other" in r.json()["detail"]


def test_rest_attach_replay_refuses_empty_first_row(client):
    res = _record(client, points=[{"name": "sp", "table": "holding", "address": 0, "datatype": "int16"},
                                  {"name": "ro", "table": "input", "address": 900}])
    assert res["missing_first"] == ["ro"]
    r = client.post("/api/scan/apply/replay", json={"device": "D"})
    assert r.status_code == 400 and "ro" in r.json()["detail"] and "first row" in r.json()["detail"]
    assert next(d for d in client.engine.config.devices if d.name == "D").points[0].behavior is None


def test_parse_range_accepts_plural_table_names():
    from sme_modbus_sim.scan import parse_range
    assert parse_range("coils:0-15") == parse_range("coil:0-15")
    assert parse_range("holdings:3").table == "holding"


def test_consecutive_integers_are_not_guessed_as_string():
    from sme_modbus_sim.scan import guess_string
    # 30502..30505 = "w&w'w(w)" in ASCII: seen on a real device that holds each register's own address.
    assert guess_string([[30502] * 3, [30503] * 3, [30504] * 3, [30505] * 3]) is None
    assert guess_string([[0x534D] * 2, [0x4152] * 2, [0x5400] * 2])["datatype"] == "string"  # "SMART"


def test_zero_high_word_pairs_are_guessed_as_uint32():
    from sme_modbus_sim.scan import suggest
    # Seen on a real device: 20 = int16, then 21-22 / 23-24 / 25-26 / 27-28 = uint32 ABCD with constant values.
    raw = {20: 30021, 21: 0, 22: 30022, 23: 0, 24: 30024, 25: 0, 26: 30026, 27: 0, 28: 30028,
           29: 30030, 30: 30031, 31: 30032, 32: 30033}
    out = {s["address"]: s for s in suggest("input", {a: [v] * 3 for a, v in raw.items()})}
    assert out[20]["datatype"] == "uint16"
    for a in (21, 23, 25, 27):
        assert out[a]["datatype"] == "uint32" and out[a]["word_order"] == "big" and out[a]["value"] == raw[a + 1]
    assert all(s["datatype"] != "string" for s in out.values())


def test_lone_zero_high_pair_stays_uint16():
    from sme_modbus_sim.scan import suggest
    out = suggest("input", {0: [0] * 3, 1: [500] * 3, 2: [7] * 3})
    assert [s["datatype"] for s in out] == ["uint16", "uint16", "uint16"]


def test_small_constant_high_word_with_changing_low_word_is_uint32():
    from sme_modbus_sim.scan import suggest
    # Seen on a real device: a counter started at 165580 = 0x0002_86CC (ABCD) and incrementing.
    out = suggest("input", {21: [2, 2, 2], 22: [34508, 34510, 34512]})
    assert out[0]["datatype"] == "uint32" and out[0]["count"] == 2 and out[0]["value"] == 2 * 65536 + 34512

"""FastAPI app: REST ``/api/*`` + WebSocket ``/ws`` + static UI.

The generic routes (``/``, ``/api/version``, ``/api/state``, ``/api/config*``, Excel import / export, device
templates, device CRUD, ``/api/simulation``, ``/ws``, the ``/sme-sim-common`` and ``/static`` mounts) come from
``sme_sim_common.web.make_base_app``. This module adds the Modbus routes: point write-back and history, the
protocol trace, per-listener statistics and fault injection ``/api/faults*`` (design 5.4; runtime state, never
written to the YAML config). Errors are ``{"detail": ..., "error": ...}``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sme_sim_common.templates import NAME_RE
from sme_sim_common.web import bad, make_base_app

from . import __version__
from . import scan as sc
from . import templates as tpl
from . import xlsx
from .config import DeviceConfig, SimConfig, atomic_write_text
from .engine import Engine

STATIC = Path(__file__).resolve().parent / "static"


class WriteReq(BaseModel):
    value: Any


def next_free_unit(cfg: SimConfig, listeners: List[str]) -> Optional[int]:
    """Lowest unit id 1..247 not used by any device sharing one of ``listeners``."""
    used = {d.unit_id for d in cfg.devices if set(cfg.device_listeners(d)) & set(listeners)}
    return next((u for u in range(1, 248) if u not in used), None)


def duplicate_unit(engine: Engine, dev: Dict[str, Any], name: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """``duplicate_hook`` of the device CRUD routes: the copy gets ``body["unit_id"]`` or the lowest unit id that
    is free on the source device's listeners."""
    unit = body.get("unit_id")
    if unit is None:
        listeners = list(dev["listeners"]) if dev.get("listeners") is not None else \
            [lc.id for lc in engine.config.listeners]
        unit = next_free_unit(engine.config, listeners)
        if unit is None:
            raise bad(400, f"no free unit id (1..247) on listeners {listeners}")
    return {**dev, "unit_id": unit}


def modbus_router(engine: Engine) -> APIRouter:
    """Modbus-specific routes (matched before the static mounts)."""
    r = APIRouter()

    def point_or_404(device: str, point: str):
        try:
            return engine._point(device, point)
        except KeyError as e:
            raise bad(404, str(e).strip("'\"")) from None

    @r.get("/api/history/{device}/{point}")
    async def history(device: str, point: str):
        point_or_404(device, point)
        return {"device": device, "point": point, "history": engine.history(device, point)}

    @r.post("/api/devices/{device}/points/{point}/write")
    async def write_point(device: str, point: str, req: WriteReq):
        point_or_404(device, point)
        try:
            value = engine.set_point(device, point, req.value)
        except (ValueError, TypeError) as e:
            raise bad(400, e) from None
        return {"device": device, "point": point, "value": value}

    @r.get("/api/trace")
    async def trace(limit: int = 200, unit: Optional[int] = None, fc: Optional[int] = None,
                    listener: Optional[str] = None):
        return engine.trace(min(max(limit, 0), 2000), unit, fc, listener)

    @r.get("/api/stats")
    async def stats():
        return engine.stats_snapshot()

    # ---- fault injection (design 5.4)

    async def json_object(request: Request) -> Dict[str, Any]:
        try:
            data = json.loads(await request.body() or b"{}")
        except ValueError as e:
            raise bad(400, f"invalid JSON: {e}") from None
        if not isinstance(data, dict):
            raise bad(400, "a fault rule is a JSON object")
        return data

    @r.get("/api/faults")
    async def faults_list():
        return {"rules": engine.faults.list()}

    @r.post("/api/faults")
    async def faults_create(request: Request):
        try:
            rule = engine.faults.add(await json_object(request))
        except ValueError as e:
            raise bad(400, e) from None
        engine.log_event("fault", f"fault rule {rule['id']} added ({rule['action']})")
        return rule

    @r.put("/api/faults/{rule_id}")
    async def faults_update(rule_id: str, request: Request):
        data = await json_object(request)
        try:
            return engine.faults.update(rule_id, data)
        except KeyError as e:
            raise bad(404, str(e).strip("'\"")) from None
        except ValueError as e:
            raise bad(400, e) from None

    @r.delete("/api/faults/{rule_id}")
    async def faults_delete(rule_id: str):
        try:
            engine.faults.delete(rule_id)
        except KeyError as e:
            raise bad(404, str(e).strip("'\"")) from None
        engine.log_event("fault", f"fault rule {rule_id} deleted")
        return {"ok": True}

    @r.post("/api/faults/clear")
    async def faults_clear():
        engine.faults.clear()
        engine.log_event("fault", "all fault rules deleted")
        return {"ok": True}

    @r.post("/api/faults/preset")
    async def faults_preset(request: Request, replace: bool = True):
        text = (await request.body()).decode("utf-8")
        try:
            rules = engine.faults.load(yaml.safe_load(text) if text.strip() else [], replace=replace)
        except yaml.YAMLError as e:
            raise bad(400, f"invalid YAML / JSON: {e}") from None
        except ValueError as e:
            raise bad(400, e) from None
        engine.log_event("fault", f"fault preset loaded ({len(rules)} rules)")
        return {"rules": rules}

    @r.get("/api/faults/export")
    async def faults_export():
        return Response(json.dumps(engine.faults.export(), indent=2, ensure_ascii=False) + "\n",
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="faults.json"'})

    add_scan_routes(r, engine)
    return r


# ---- scan / record of a real device (design 20261125-0003; always available, decision S5)

def _num(body: Dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    v = body.get(key, default)
    if v is None:
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise bad(400, f"{key}: not a number") from None
    if not lo <= v <= hi:
        raise bad(400, f"{key}: must be in {lo:g}..{hi:g}")
    return v


def _scan_request(body: Dict[str, Any]):
    """(client, unit, ranges, scan kwargs, plan) from a scan request body; nothing is connected."""
    rate = _num(body, "rate", 10, 0.1, 100)
    timeout = _num(body, "timeout", 1, 0.05, 30)
    samples = int(_num(body, "samples", 3, 1, 10))
    interval = _num(body, "sample_interval", 1, 0, 60)
    block = body.get("block_size")
    block = int(_num(body, "block_size", 0, 1, 125)) if block is not None else None
    max_timeouts = int(_num(body, "max_timeouts", 3, 1, 100))
    try:
        unit = sc.check_unit(body.get("unit", 1))
        ranges = sc.parse_ranges(body.get("ranges") or [])
        client = sc.make_client(body.get("target") or {}, timeout=timeout, rate=rate)
    except ValueError as e:
        raise bad(400, e) from None
    plan = sc.scan_plan(client.endpoint, unit, ranges, rate, samples, interval, block, timeout)
    kw = {"samples": samples, "sample_interval": interval, "block_size": block, "max_timeouts": max_timeouts}
    return client, unit, ranges, kw, plan


def _record_request(engine: Engine, body: Dict[str, Any]):
    """(client, unit, points, (byte_order, word_order), period, duration, csv path, plan)."""
    rate = _num(body, "rate", 10, 0.1, 100)
    timeout = _num(body, "timeout", 1, 0.05, 30)
    period = _num(body, "period", 1, 0.1, 3600)
    duration = _num(body, "duration", 60, 0, 7 * 86400)
    sources = [k for k in ("device", "template", "points") if body.get(k)]
    if len(sources) != 1:
        raise bad(400, "give exactly one of device (a configured device name), template (a template name) "
                       "or points (a point list)")
    if sources == ["device"]:
        dev = next((d for d in engine.config.devices if d.name == body["device"]), None)
        if dev is None:
            raise bad(404, f"device {body['device']!r} not found")
    try:
        unit = sc.check_unit(body.get("unit", 1))
        client = sc.make_client(body.get("target") or {}, timeout=timeout, rate=rate)
        if sources == ["template"]:
            dev = DeviceConfig(name="record", unit_id=1, template=str(body["template"]))
        elif sources == ["points"]:
            dev = DeviceConfig(name="record", unit_id=1, points=body["points"])
        points = sc.record_points(tpl.expand_device(dev))
        plan = sc.record_plan(client.endpoint, unit, points, period, duration, rate)
    except ValueError as e:
        raise bad(400, e) from None
    name = str(body.get("file") or f"record-{time.strftime('%Y%m%d-%H%M%S')}.csv")
    if Path(name).name != name or not name.lower().endswith(".csv"):
        raise bad(400, "file: a plain file name ending in .csv (saved next to the config file)")
    path = Path(engine.base_dir or Path.cwd()) / name
    plan["file"] = str(path)
    return client, unit, points, (dev.byte_order, dev.word_order), period, duration, path, plan


def add_scan_routes(r: APIRouter, engine: Engine) -> None:
    jobs = sc.ScanJobs(engine.publish, engine.log_event)
    engine.scan_jobs = jobs

    async def body_of(request: Request) -> Dict[str, Any]:
        try:
            data = json.loads(await request.body() or b"{}")
        except ValueError as e:
            raise bad(400, f"invalid JSON: {e}") from None
        if not isinstance(data, dict):
            raise bad(400, "expected a JSON object")
        return data

    def start(kind, plan, client, run):
        try:
            return jobs.start(kind, plan, client, run)
        except sc.JobBusy as e:
            raise bad(409, e) from None

    @r.post("/api/scan/plan")
    async def scan_plan(request: Request):
        return _scan_request(await body_of(request))[4]

    @r.post("/api/scan/start")
    async def scan_start(request: Request):
        client, unit, ranges, kw, plan = _scan_request(await body_of(request))
        return start("scan", plan, client, lambda progress, cancel: sc.scan(
            client, unit, ranges, progress=progress, cancel=cancel, **kw))

    @r.post("/api/record/plan")
    async def record_plan(request: Request):
        return _record_request(engine, await body_of(request))[7]

    @r.post("/api/record/start")
    async def record_start(request: Request):
        client, unit, points, orders, period, duration, path, plan = _record_request(engine, await body_of(request))
        return start("record", plan, client, lambda progress, cancel: sc.record(
            client, unit, points, period, duration, path, byte_order=orders[0], word_order=orders[1],
            progress=progress, cancel=cancel))

    @r.get("/api/scan/job")
    async def job_get():
        job = jobs.summary(with_result=True)
        if job is None:
            raise bad(404, "no scan / record job yet")
        return job

    @r.post("/api/scan/job/cancel")
    async def job_cancel():
        if not jobs.cancel():
            raise bad(409, "no running job")
        return {"ok": True}

    @r.get("/api/scan/job/csv")
    async def job_csv():
        job = jobs.job
        if not job or job["kind"] != "record" or not job.get("result"):
            raise bad(404, "no finished record job")
        path = Path(job["result"]["path"])
        if not path.is_file():
            raise bad(404, f"{path} no longer exists")
        return FileResponse(path, media_type="text/csv", filename=path.name)

    def scan_result() -> Dict[str, Any]:
        job = jobs.job
        if not job or job["kind"] != "scan" or job["state"] != "done" or not job.get("result"):
            raise bad(409, "no finished scan to apply")
        return job["result"]

    @r.post("/api/scan/apply/template")
    async def apply_template(request: Request):
        body = await body_of(request)
        result = scan_result()
        name = body.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise bad(400, "name: required (letters, digits, _ . -)")
        try:
            doc = sc.to_template(result, body.get("edits"), name=name, title=body.get("title"),
                                 description=body.get("description"))
        except ValueError as e:
            raise bad(400, e) from None
        tdir = tpl.templates_dir()
        path = tdir / f"{name}.yaml"
        if path.exists() and not body.get("overwrite"):
            raise bad(409, f"{path} already exists (set overwrite to replace it)")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, sc.template_yaml(doc, result))
        except OSError as e:
            raise bad(400, f"cannot write {path}: {e.strerror or e}") from None
        mine = [e for e in tpl.load_user_templates(tdir) if Path(e["path"]) == path]
        if mine:
            raise bad(400, f"saved {path} but it does not load: {mine[0]['error']}")
        engine.log_event("config", f"scan result saved as user template {name!r} ({path})")
        return {"ok": True, "name": name, "path": str(path), "points": len(doc["points"])}

    @r.post("/api/scan/apply/device")
    async def apply_device(request: Request):
        body = await body_of(request)
        result = scan_result()
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise bad(400, "name: required")
        if any(d.name == name for d in engine.config.devices):
            raise bad(409, f"device {name!r} already exists")
        listeners = body.get("listeners")
        unit = body.get("unit_id")
        if unit is None:
            unit = next_free_unit(engine.config, listeners if listeners is not None else
                                  [lc.id for lc in engine.config.listeners])
            if unit is None:
                raise bad(400, "no free unit id (1..247)")
        try:
            dev = sc.to_device(result, body.get("edits"), name=name, unit_id=int(unit), listeners=listeners)
            data = engine.config.model_dump(mode="json", exclude_unset=True, exclude_none=True)
            data["devices"] = [*data.get("devices", []), dev]
            cfg = SimConfig.model_validate(data)
        except (TypeError, ValueError) as e:
            raise bad(400, e) from None
        try:
            await engine.reload(cfg, engine.dump_config(cfg))
        except Exception as e:  # noqa: BLE001 - the old config keeps running
            raise bad(400, e) from None
        engine.log_event("config", f"device {name} added from scan of {result.get('target')}")
        return {"ok": True, "name": name, "device": dev}


def create_app(engine: Engine, config_path: Optional[str | Path] = None) -> FastAPI:
    return make_base_app(engine, config_path=config_path, static_dir=STATIC, title="SME ModbusSim",
                         version=__version__, config_model=SimConfig, device_model=DeviceConfig,
                         xlsx=xlsx.SCHEMA, templates=tpl.registry, duplicate_hook=duplicate_unit,
                         product_routers=[modbus_router(engine)])

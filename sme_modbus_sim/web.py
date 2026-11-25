"""FastAPI app: REST ``/api/*`` + WebSocket ``/ws`` + static UI.

App factory, ``_bad()`` error mapping and the ``/ws`` snapshot/dedup loop follow sme-bac-sim
``sme_bacnet_sim/web.py`` @ ebd27ec.
Excel import / export (design 5.6) and user-local template routes (design 5.3.1) are ModbusSim additions.
Fault injection routes ``/api/faults*`` (design 5.4) are runtime state, never written to the YAML config.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
import yaml
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import __version__
from .config import DeviceConfig, SimConfig, parse_text
from .engine import Engine
from . import templates as tpl
from .templates import template_catalog
from .xlsx import EXPORT_FILE, TEMPLATE_FILE, blank_template, export_xlsx, import_xlsx, to_yaml

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

STATIC = Path(__file__).resolve().parent / "static"


class WriteReq(BaseModel):
    value: Any


class SaveTemplateReq(BaseModel):
    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    overwrite: bool = False


class SimulationReq(BaseModel):
    running: Optional[bool] = None
    time_scale: Optional[float] = None


class DuplicateReq(BaseModel):
    name: Optional[str] = None
    unit_id: Optional[int] = None


def next_free_unit(cfg: SimConfig, listeners: List[str]) -> Optional[int]:
    """Lowest unit id 1..247 not used by any device sharing one of ``listeners``."""
    used = {d.unit_id for d in cfg.devices if set(cfg.device_listeners(d)) & set(listeners)}
    return next((u for u in range(1, 248) if u not in used), None)


def _bad(e: Exception) -> HTTPException:
    if isinstance(e, ValidationError):
        parts = []
        for err in e.errors():
            loc, text = ".".join(str(x) for x in err["loc"]), str(err["msg"]).removeprefix("Value error, ")
            parts.append(f"{loc}: {text}" if loc else text)
        msg = "; ".join(parts)
    else:
        msg = str(e)
    return HTTPException(status_code=400, detail=msg)


def create_app(engine: Engine, config_path: Optional[str | Path] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(title="SME ModbusSim", version=__version__, lifespan=lifespan)

    def point_or_404(device: str, point: str):
        try:
            return engine._point(device, point)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from None

    @app.get("/api/version")
    async def version():
        return {"name": "SME ModbusSim", "version": __version__}

    @app.get("/api/state")
    async def state():
        return engine.snapshot()

    @app.get("/api/templates")
    async def templates():
        return template_catalog()

    @app.post("/api/templates/reload")
    async def reload_templates():
        errors = tpl.load_user_templates()
        engine.log_event("config", f"user templates reloaded from {tpl.templates_dir()} "
                                   f"({len(tpl.USER_TEMPLATES)} loaded, {len(errors)} with errors)")
        return {"dir": str(tpl.templates_dir()), "loaded": sorted(tpl.USER_TEMPLATES), "errors": errors,
                "templates": template_catalog()}

    @app.post("/api/templates/from-device/{device}")
    async def template_from_device(device: str, req: SaveTemplateReq):
        dev = next((d for d in engine.config.devices if d.name == device), None)
        if dev is None:
            raise HTTPException(404, f"unknown device {device!r}")
        try:
            text = tpl.device_as_template(dev, req.name, req.title, req.description)
        except ValueError as e:
            raise _bad(e) from None
        path = tpl.templates_dir() / f"{req.name}.yaml"
        if path.exists() and not req.overwrite:
            raise HTTPException(409, f"{path} already exists (set overwrite to replace it)")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            raise HTTPException(400, f"cannot write {path}: {e.strerror or e}") from None
        errors = tpl.load_user_templates()
        mine = [e for e in errors if Path(e["path"]) == path]
        if mine:
            raise HTTPException(400, f"saved {path} but it does not load: {mine[0]['error']}")
        engine.log_event("config", f"device {device} saved as user template {req.name!r} ({path})")
        return {"ok": True, "name": req.name, "path": str(path)}

    @app.get("/api/history/{device}/{point}")
    async def history(device: str, point: str):
        point_or_404(device, point)
        return {"device": device, "point": point, "history": engine.history(device, point)}

    @app.post("/api/devices/{device}/points/{point}/write")
    async def write_point(device: str, point: str, req: WriteReq):
        point_or_404(device, point)
        try:
            value = engine.set_point(device, point, req.value)
        except (ValueError, TypeError) as e:
            raise _bad(e) from None
        return {"device": device, "point": point, "value": value}

    @app.post("/api/simulation")
    async def simulation(req: SimulationReq):
        try:
            engine.set_running(req.running, req.time_scale)
        except ValueError as e:
            raise _bad(e) from None
        sim = engine.config.simulation
        return {"running": sim.running, "time_scale": sim.time_scale}

    # ---- device management: edits the configuration model and re-dumps it like the Excel import (comments are lost)

    def device_dicts() -> List[Dict[str, Any]]:
        return [d.model_dump(mode="json", exclude_unset=True, exclude_none=True) for d in engine.config.devices]

    def device_index(name: str) -> int:
        for i, d in enumerate(engine.config.devices):
            if d.name == name:
                return i
        raise HTTPException(404, f"unknown device {name!r}")

    def name_free(name: Any, skip: Optional[str] = None) -> None:
        if any(d.name == name and d.name != skip for d in engine.config.devices):
            raise HTTPException(409, f"device {name!r} already exists")

    async def apply_devices(devices: List[Dict[str, Any]]) -> None:
        data = engine.config.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        data["devices"] = devices
        try:
            cfg = SimConfig.model_validate(data)
            await engine.reload(cfg, to_yaml(cfg))
        except Exception as e:  # validation, behavior compile or bind errors; the old config keeps running
            raise _bad(e) from None

    def parse_device(data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise HTTPException(400, "a device is a JSON / YAML object")
        try:
            dev = DeviceConfig.model_validate(data)
        except ValidationError as e:
            raise _bad(e) from None
        return dev.model_dump(mode="json", exclude_unset=True, exclude_none=True)

    @app.post("/api/devices")
    async def device_create(request: Request):
        try:
            data = json.loads(await request.body() or b"{}")
        except ValueError as e:
            raise HTTPException(400, f"invalid JSON: {e}") from None
        dev = parse_device(data)
        name_free(dev["name"])
        await apply_devices(device_dicts() + [dev])
        engine.log_event("config", f"device {dev['name']} added (unit {dev['unit_id']})")
        return {"ok": True, "name": dev["name"], "unit_id": dev["unit_id"]}

    @app.post("/api/devices/{device}/duplicate")
    async def device_duplicate(device: str, req: Optional[DuplicateReq] = None):
        req = req or DuplicateReq()
        src = engine.config.devices[device_index(device)]
        names = {d.name for d in engine.config.devices}
        name = req.name or next(n for n in (f"{device}_copy", *(f"{device}_copy{i}" for i in range(2, 10000)))
                                if n not in names)
        name_free(name)
        unit = req.unit_id or next_free_unit(engine.config, engine.config.device_listeners(src))
        if unit is None:
            raise HTTPException(400, f"no free unit id (1..247) on listeners {engine.config.device_listeners(src)}")
        dev = src.model_dump(mode="json", exclude_unset=True, exclude_none=True) | {"name": name, "unit_id": unit}
        await apply_devices(device_dicts() + [dev])
        engine.log_event("config", f"device {device} duplicated as {name} (unit {unit})")
        return {"ok": True, "name": name, "unit_id": unit}

    @app.delete("/api/devices/{device}")
    async def device_delete(device: str):
        i = device_index(device)
        devices = device_dicts()
        del devices[i]
        await apply_devices(devices)
        engine.log_event("config", f"device {device} deleted")
        return {"ok": True}

    @app.get("/api/devices/{device}/config", response_class=PlainTextResponse)
    async def device_config_get(device: str):
        d = engine.config.devices[device_index(device)]
        text = yaml.safe_dump(d.model_dump(mode="json", exclude_unset=True, exclude_none=True),
                              allow_unicode=True, sort_keys=False)
        return PlainTextResponse(text, media_type="application/x-yaml")

    @app.put("/api/devices/{device}/config")
    async def device_config_put(device: str, request: Request):
        i = device_index(device)
        try:
            data = yaml.safe_load((await request.body()).decode("utf-8"))
        except yaml.YAMLError as e:
            raise HTTPException(400, f"invalid YAML: {e}") from None
        dev = parse_device(data)
        name_free(dev["name"], skip=device)
        devices = device_dicts()
        devices[i] = dev
        await apply_devices(devices)
        engine.log_event("config", f"device {device} updated" + (f" (renamed to {dev['name']})"
                                                                 if dev["name"] != device else ""))
        return {"ok": True, "name": dev["name"]}

    @app.get("/api/config", response_class=PlainTextResponse)
    async def get_config():
        return PlainTextResponse(engine.config_text, media_type="application/x-yaml")

    @app.put("/api/config")
    async def put_config(request: Request):
        text = (await request.body()).decode("utf-8")
        try:
            cfg = parse_text(text)
            await engine.reload(cfg, text)
        except Exception as e:  # validation, YAML syntax, behavior or bind errors
            raise _bad(e) from None
        return {"ok": True, "devices": len(engine.devices)}

    @app.get("/api/config/xlsx")
    async def config_xlsx():
        try:
            data = export_xlsx(engine.config)
        except ValueError as e:
            raise _bad(e) from None
        return Response(data, media_type=XLSX_TYPE,
                        headers={"Content-Disposition": f'attachment; filename="{EXPORT_FILE}"'})

    @app.get("/api/config/xlsx-template")
    async def config_xlsx_template():
        return Response(blank_template(), media_type=XLSX_TYPE,
                        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILE}"'})

    @app.post("/api/config/import")
    async def config_import(file: UploadFile = File(...)):
        cfg, msgs = import_xlsx(await file.read(), engine.config)
        if cfg is None:
            raise HTTPException(400, {"message": f"{len(msgs)} error(s) in {file.filename or 'the workbook'}",
                                      "errors": msgs})
        try:
            await engine.reload(cfg, to_yaml(cfg))
        except Exception as e:  # behavior compile / bind errors
            raise HTTPException(400, {"message": "the imported configuration cannot be applied",
                                      "errors": [str(e)]}) from None
        engine.log_event("config", f"Excel point table imported ({file.filename}, {len(engine.devices)} devices)")
        return {"ok": True, "devices": len(engine.devices), "warnings": msgs}

    @app.post("/api/config/save")
    async def save_config():
        if not config_path:
            raise HTTPException(400, "no config file (started without -c); nothing to save to")
        try:
            Path(config_path).write_text(engine.config_text, encoding="utf-8")
        except OSError as e:
            raise HTTPException(400, f"cannot write {config_path}: {e.strerror or e}") from None
        engine.log_event("config", f"configuration saved to {config_path}")
        return {"ok": True, "path": str(config_path)}

    @app.get("/api/trace")
    async def trace(limit: int = 200, unit: Optional[int] = None, fc: Optional[int] = None,
                    listener: Optional[str] = None):
        return engine.trace(min(max(limit, 0), 2000), unit, fc, listener)

    @app.get("/api/stats")
    async def stats():
        return engine.stats_snapshot()

    # ---- fault injection (design 5.4)

    async def json_object(request: Request) -> Dict[str, Any]:
        try:
            data = json.loads(await request.body() or b"{}")
        except ValueError as e:
            raise HTTPException(400, f"invalid JSON: {e}") from None
        if not isinstance(data, dict):
            raise HTTPException(400, "a fault rule is a JSON object")
        return data

    @app.get("/api/faults")
    async def faults_list():
        return {"rules": engine.faults.list()}

    @app.post("/api/faults")
    async def faults_create(request: Request):
        try:
            rule = engine.faults.add(await json_object(request))
        except ValueError as e:
            raise _bad(e) from None
        engine.log_event("fault", f"fault rule {rule['id']} added ({rule['action']})")
        return rule

    @app.put("/api/faults/{rule_id}")
    async def faults_update(rule_id: str, request: Request):
        data = await json_object(request)
        try:
            return engine.faults.update(rule_id, data)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from None
        except ValueError as e:
            raise _bad(e) from None

    @app.delete("/api/faults/{rule_id}")
    async def faults_delete(rule_id: str):
        try:
            engine.faults.delete(rule_id)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from None
        engine.log_event("fault", f"fault rule {rule_id} deleted")
        return {"ok": True}

    @app.post("/api/faults/clear")
    async def faults_clear():
        engine.faults.clear()
        engine.log_event("fault", "all fault rules deleted")
        return {"ok": True}

    @app.post("/api/faults/preset")
    async def faults_preset(request: Request, replace: bool = True):
        text = (await request.body()).decode("utf-8")
        try:
            rules = engine.faults.load(yaml.safe_load(text) if text.strip() else [], replace=replace)
        except yaml.YAMLError as e:
            raise HTTPException(400, f"invalid YAML / JSON: {e}") from None
        except ValueError as e:
            raise _bad(e) from None
        engine.log_event("fault", f"fault preset loaded ({len(rules)} rules)")
        return {"rules": rules}

    @app.get("/api/faults/export")
    async def faults_export():
        return Response(json.dumps(engine.faults.export(), indent=2, ensure_ascii=False) + "\n",
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="faults.json"'})

    # ---- live updates

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        q = engine.subscribe()
        try:
            def snap() -> Dict[str, Any]:
                nonlocal seen
                data = engine.snapshot()
                seen = max((e.get("seq", 0) for e in data.get("events", [])), default=seen)
                return {"type": "snapshot", "data": data}

            seen = 0
            await sock.send_json(snap())
            while True:
                msg = await q.get()
                if msg["type"] == "reload":
                    msg = snap()
                elif msg["type"] == "log" and msg["event"].get("seq", 0) <= seen:
                    continue  # already contained in a snapshot sent after it was logged
                await sock.send_json(msg)
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            engine.unsubscribe(q)

    @app.get("/")
    async def index():
        page = STATIC / "index.html"
        if page.is_file():
            return FileResponse(page)
        return PlainTextResponse(f"SME ModbusSim {__version__}: web UI not built; the REST API is at /api/state")

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app

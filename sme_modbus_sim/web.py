"""FastAPI app: REST ``/api/*`` + WebSocket ``/ws`` + static UI.

The generic routes (``/``, ``/api/version``, ``/api/state``, ``/api/config*``, Excel import / export, device
templates, device CRUD, ``/api/simulation``, ``/ws``, the ``/sme-sim-common`` and ``/static`` mounts) come from
``sme_sim_common.web.make_base_app``. This module adds the Modbus routes: point write-back and history, the
protocol trace, per-listener statistics and fault injection ``/api/faults*`` (design 5.4; runtime state, never
written to the YAML config). Errors are ``{"detail": ..., "error": ...}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sme_sim_common.web import bad, make_base_app

from . import __version__
from . import templates as tpl
from . import xlsx
from .config import DeviceConfig, SimConfig
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

    return r


def create_app(engine: Engine, config_path: Optional[str | Path] = None) -> FastAPI:
    return make_base_app(engine, config_path=config_path, static_dir=STATIC, title="SME ModbusSim",
                         version=__version__, config_model=SimConfig, device_model=DeviceConfig,
                         xlsx=xlsx.SCHEMA, templates=tpl.registry, duplicate_hook=duplicate_unit,
                         product_routers=[modbus_router(engine)])

"""FastAPI app: REST ``/api/*`` + WebSocket ``/ws`` + static UI.

App factory, ``_bad()`` error mapping and the ``/ws`` snapshot/dedup loop follow sme-bac-sim
``sme_bacnet_sim/web.py`` @ ebd27ec.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import __version__
from .config import parse_text
from .engine import Engine
from .templates import template_catalog

STATIC = Path(__file__).resolve().parent / "static"


class WriteReq(BaseModel):
    value: Any


class SimulationReq(BaseModel):
    running: Optional[bool] = None
    time_scale: Optional[float] = None


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

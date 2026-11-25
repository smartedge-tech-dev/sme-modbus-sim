"""Command line entry point.

  sme-modbus-sim [run] [-c CONFIG] [--host 127.0.0.1] [--http-port 8081] [--no-browser]
  sme-modbus-sim validate CONFIG
  sme-modbus-sim templates [--templates-dir DIR]

User-local templates are read from --templates-dir, $SME_MODBUS_SIM_TEMPLATES or ~/.sme-modbus-sim/templates.
  sme-modbus-sim --version

Default-config resolution follows sme-bac-sim ``sme_bacnet_sim/__main__.py`` @ ebd27ec.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "demo-site.yaml"


def _load_templates(directory) -> None:
    from .templates import load_user_templates

    for e in load_user_templates(directory):
        print(f"user template {e['path']}: skipped: {e['error']}", file=sys.stderr)


def _tdir_arg(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--templates-dir", help="user template directory "
                                            "(default: $SME_MODBUS_SIM_TEMPLATES or ~/.sme-modbus-sim/templates)")


def _validate(argv) -> int:
    from .config import load_file

    ap = argparse.ArgumentParser(prog="sme-modbus-sim validate", description="check a config file")
    ap.add_argument("config")
    _tdir_arg(ap)
    args = ap.parse_args(argv)
    _load_templates(args.templates_dir)
    try:
        cfg = load_file(args.config)
    except Exception as e:
        print(f"{args.config}: invalid\n{e}", file=sys.stderr)
        return 1
    try:
        from .engine import Engine
        Engine(cfg, Path(args.config).resolve().parent)  # behaviors / initial values
    except Exception as e:
        print(f"{args.config}: invalid\n{e}", file=sys.stderr)
        return 1
    print(f"{args.config}: OK ({len(cfg.listeners)} listeners, {len(cfg.devices)} devices)")
    return 0


def _templates(argv) -> int:
    from .templates import template_catalog

    ap = argparse.ArgumentParser(prog="sme-modbus-sim templates", description="list device templates")
    _tdir_arg(ap)
    _load_templates(ap.parse_args(argv).templates_dir)
    for t in template_catalog():
        params = ", ".join(f"{k}={v!r}" for k, v in t["params"].items())
        src = f"  [user: {t['path']}]" if t["source"] == "user" else ""
        print(f"{t['name']:<14} {t['title']}{src}" + (f"\n{'':<14} params: {params}" if params else ""))
    return 0


def main(argv=None) -> int:
    rest = sys.argv[1:] if argv is None else list(argv)
    if rest[:1] == ["validate"]:
        return _validate(rest[1:])
    if rest[:1] == ["templates"]:
        return _templates(rest[1:])
    if rest[:1] == ["run"]:
        rest = rest[1:]
    ap = argparse.ArgumentParser(prog="sme-modbus-sim",
                                 description="Modbus TCP / RTU over TCP device simulator with a web UI "
                                             "(subcommands: run, validate CONFIG, templates)")
    ap.add_argument("--version", action="version", version=f"SME ModbusSim {__version__}")
    ap.add_argument("-c", "--config", help="YAML/JSON config file (default: bundled configs/demo-site.yaml)")
    ap.add_argument("--host", default="127.0.0.1", help="web UI host (use 0.0.0.0 to expose on the LAN)")
    ap.add_argument("--http-port", type=int, default=8081, help="web UI port")
    ap.add_argument("--no-browser", action="store_true", help="do not open the web UI in a browser")
    _tdir_arg(ap)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(rest)

    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .config import SimConfig, load_file
    from .engine import Engine

    _load_templates(args.templates_dir)  # before the config: devices may use user templates
    path = args.config
    if path is None and DEFAULT_CONFIG.exists():
        path = str(DEFAULT_CONFIG)
    try:
        cfg = load_file(path) if path else SimConfig()
        text = Path(path).read_text(encoding="utf-8") if path else None
        engine = Engine(cfg, Path(path).resolve().parent if path else None, config_text=text)
    except Exception as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    import uvicorn

    from .web import create_app

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.http_port}/"
    print(f"SME ModbusSim v{__version__} web UI: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(engine, path), host=args.host, port=args.http_port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())

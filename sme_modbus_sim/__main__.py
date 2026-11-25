"""Command line entry point.

  sme-modbus-sim [run] [-c CONFIG] [--host 127.0.0.1] [--http-port 8081] [--no-browser]
  sme-modbus-sim validate CONFIG          (exit 0 = valid, 2 = config error)
  sme-modbus-sim templates [--templates-dir DIR]

User-local templates are read from --templates-dir, $SME_MODBUS_SIM_TEMPLATES or ~/.sme-modbus-sim/templates.
  sme-modbus-sim --version

The common options (-c, --host, --http-port, --no-browser, --templates-dir, -v, --version), the web server start
and ``validate`` come from ``sme_sim_common.cli``; default-config resolution follows sme-bac-sim
``sme_bacnet_sim/__main__.py`` @ ebd27ec.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sme_sim_common.cli import add_common_args, run_validate, run_web

from . import __version__

APP_NAME = "sme-modbus-sim"
DEFAULT_HTTP_PORT = 8081  # series port table (sme-sim-common README)
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "demo-site.yaml"


def _load_templates(directory) -> None:
    from .templates import load_user_templates

    for e in load_user_templates(directory):
        print(f"user template {e['path']}: skipped: {e['error']}", file=sys.stderr)


def _validate(argv) -> int:
    from .config import load_file
    from .engine import Engine

    return run_validate(argv, prog=APP_NAME, load=load_file,
                        before=lambda args: _load_templates(args.templates_dir),
                        build=lambda cfg, path: Engine(cfg, path.resolve().parent),  # behaviors / initial values
                        summary=lambda cfg: f"{len(cfg.listeners)} listeners, {len(cfg.devices)} devices")


def _templates(argv) -> int:
    from .templates import ENV_TEMPLATES_DIR, template_catalog

    ap = argparse.ArgumentParser(prog=f"{APP_NAME} templates", description="list device templates")
    ap.add_argument("--templates-dir", help=f"user template directory "
                                            f"(default: ${ENV_TEMPLATES_DIR} or ~/.{APP_NAME}/templates)")
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
    ap = argparse.ArgumentParser(prog=APP_NAME,
                                 description="Modbus TCP / RTU over TCP device simulator with a web UI "
                                             "(subcommands: run, validate CONFIG, templates); "
                                             "default config: the bundled configs/demo-site.yaml")
    add_common_args(ap, default_http_port=DEFAULT_HTTP_PORT, app_name=APP_NAME, version=__version__)
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
        engine = Engine(cfg, Path(path).resolve().parent if path else None, config_text=text, config_path=path)
    except Exception as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    from .web import create_app

    run_web(create_app(engine, path), args, name=f"SME ModbusSim v{__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

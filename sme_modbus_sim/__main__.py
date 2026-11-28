"""Command line entry point.

  sme-modbus-sim [run] [-c CONFIG] [--host 127.0.0.1] [--http-port 8081] [--no-browser]
  sme-modbus-sim validate [-c] CONFIG     (exit 0 = valid, 2 = config error)
  sme-modbus-sim templates [--templates-dir DIR]
  sme-modbus-sim scan   (--host H [--port 502] [--transport tcp|rtu_over_tcp|udp|tls] | --serial COM5 [--baudrate ...])
                        --unit 1 --range holding:0-99 [--range ...] [-o template.yaml] [-y]
  sme-modbus-sim record (target as for scan) --unit 1 (--template FILE_OR_NAME | -c CONFIG --device NAME)
                        [--period 1] [--duration 60] [-o out.csv] [-y]
  scan / record are read-only (FC 01/02/03/04 and 2B/0E) and ask for confirmation unless -y is given.

User-local templates are read from --templates-dir, $SME_MODBUS_SIM_TEMPLATES or ~/.sme-modbus-sim/templates.
  sme-modbus-sim --version

The common options (-c, --host, --http-port, --no-browser, --templates-dir, -v, --version), the web server start
and ``validate`` come from ``sme_sim_common.cli``; default-config resolution follows sme-bac-sim
``sme_bacnet_sim/__main__.py`` @ ebd27ec.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional

from sme_sim_common.cli import add_common_args, run_validate, run_web

from . import __version__

APP_NAME = "sme-modbus-sim"
DEFAULT_HTTP_PORT = 8081  # series port table (sme-sim-common README)
_PKG = Path(__file__).resolve().parent
# Bundled demo config. Installed wheel: sme_modbus_sim/configs; source tree, Docker image and Windows exe
# (PyInstaller puts the package under sys._MEIPASS, so __file__ resolves there): configs/ beside the package.
DEFAULT_CONFIG = next((p for p in (_PKG / "configs" / "demo-site.yaml", _PKG.parent / "configs" / "demo-site.yaml")
                       if p.exists()), _PKG.parent / "configs" / "demo-site.yaml")
USER_CONFIG_NAME = "config.yaml"

log = logging.getLogger(APP_NAME)


def user_data_dir() -> Path:
    """Per-user data directory ``~/.sme-modbus-sim``: user templates (``templates/``, unless
    ``$SME_MODBUS_SIM_TEMPLATES`` is set) and, for the Windows executable, the default config file
    (``config.yaml``, see :func:`default_config_path`)."""
    return Path.home() / f".{APP_NAME}"


def default_config_path() -> Optional[Path]:
    """Config file used when started without ``-c``.

    Windows executable (PyInstaller onefile, ``sys.frozen``): the bundled config lives in the temporary
    extraction directory (``sys._MEIPASS``) that is deleted when the exe exits, so saving from the web UI there
    would be lost. The default is the per-user ``~/.sme-modbus-sim/config.yaml`` instead; on the first run it is
    created as a copy of the bundled demo config. Recordings saved next to the config file then land in the same
    folder. Nothing is ever written below ``sys._MEIPASS``.

    Everything else (pip, source tree, Docker): the bundled demo config itself, as before.
    """
    if not getattr(sys, "frozen", False):
        return DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None
    user = user_data_dir() / USER_CONFIG_NAME
    if not user.exists() and DEFAULT_CONFIG.exists():
        user.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_CONFIG, user)
        log.info("created %s from the bundled %s", user, DEFAULT_CONFIG.name)
    return user if user.exists() else None


def _progress(text: str) -> None:
    """One progress line on stderr: rewritten in place on a terminal, one line each when redirected."""
    if sys.stderr.isatty():
        print("\r" + text, end="", file=sys.stderr, flush=True)
    else:
        print(text, file=sys.stderr, flush=True)


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


def _target_args(ap: argparse.ArgumentParser) -> None:
    g = ap.add_argument_group("target (read-only: FC 01/02/03/04 and 2B/0E only)")
    g.add_argument("--host", help="TCP host of the device or gateway")
    g.add_argument("--port", type=int, help="TCP / UDP port (default 502, tls 802)")
    g.add_argument("--transport", choices=("tcp", "rtu_over_tcp", "udp", "tls"), default="tcp", help="network framing")
    g.add_argument("--ca", help="tls: CA certificate (PEM) to verify the server; without it the server is not verified")
    g.add_argument("--cert", help="tls: client certificate (PEM)")
    g.add_argument("--key", help="tls: client private key (PEM)")
    g.add_argument("--serial", metavar="DEVICE", help="serial device or pyserial URL (RTU), e.g. COM5")
    g.add_argument("--baudrate", type=int, default=19200)
    g.add_argument("--parity", choices=("E", "N", "O"), default="E")
    g.add_argument("--stopbits", type=int, choices=(1, 2))
    g.add_argument("--unit", type=int, default=1, help="unit id (default 1)")
    g.add_argument("--rate", type=float, default=10.0, help="max requests per second (default 10)")
    g.add_argument("--timeout", type=float, default=1.0, help="per-request timeout in seconds (default 1)")
    g.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")


def _target(args) -> dict:
    if bool(args.host) == bool(args.serial):
        raise ValueError("give either --host (TCP) or --serial (RTU)")
    if args.serial:
        return {"type": "rtu", "port": args.serial, "baudrate": args.baudrate, "parity": args.parity,
                "stopbits": args.stopbits}
    t = {"type": args.transport, "host": args.host, "port": args.port}
    for k, v in (("ca", args.ca), ("certificate", args.cert), ("private_key", args.key)):
        if v:
            t[k] = v
    return t


def _confirm(plan: dict, yes: bool, extra: list) -> int:
    """Print the plan; returns 0 to go on, otherwise the exit code."""
    lines = [f"{plan['kind']}: {plan['target']} unit {plan['unit']}", *extra,
             f"estimated requests: {plan['requests']}, duration: ~{plan['duration_s']} s "
             f"at <= {plan['rate']:g} requests/s", plan["read_only"]]
    print("\n".join(lines), file=sys.stderr)
    if yes:
        return 0
    if sys.stdin is None or not sys.stdin.isatty():
        print("aborted: confirmation needed but stdin is not interactive (use -y / --yes)", file=sys.stderr)
        return 2
    try:
        answer = input("Proceed? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("aborted", file=sys.stderr)
        return 1
    return 0


def _scan(argv) -> int:
    import asyncio

    from . import scan as sc

    ap = argparse.ArgumentParser(prog=f"{APP_NAME} scan",
                                 description="read-only scan of a real device into a user template (YAML)")
    _target_args(ap)
    ap.add_argument("--range", action="append", required=True, metavar="TABLE:START-END",
                    help="address range to scan (repeatable), e.g. holding:0-99, coil:0-15")
    ap.add_argument("--samples", type=int, default=3, help="reads per address for type guessing (default 3)")
    ap.add_argument("--sample-interval", type=float, default=1.0, help="seconds between samples (default 1)")
    ap.add_argument("--name", default="scanned", help="template name (default scanned)")
    ap.add_argument("-o", "--output", help="output YAML (default: <name>.yaml)")
    args = ap.parse_args(argv)
    try:
        ranges = sc.parse_ranges(args.range)
        client = sc.make_client(_target(args), timeout=args.timeout, rate=args.rate)
        plan = sc.scan_plan(client.endpoint, sc.check_unit(args.unit), ranges, args.rate, args.samples,
                            args.sample_interval, timeout=args.timeout)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rc = _confirm(plan, args.yes, ["ranges: " + ", ".join(f"{r.table}:{r.start}-{r.start + r.count - 1}"
                                                          for r in ranges)])
    if rc:
        return rc

    def progress(p):
        _progress(f"{p['phase']}: {p['requests']}/{p['estimate']} requests, {p['found']} readable")

    async def run():
        async with client:
            return await sc.scan(client, args.unit, ranges, samples=args.samples,
                                 sample_interval=args.sample_interval, progress=progress)

    try:
        result = asyncio.run(run())
        doc = sc.to_template(result, name=args.name)
    except Exception as e:  # noqa: BLE001
        print(f"\nscan failed: {e}", file=sys.stderr)
        return 1
    out = Path(args.output or f"{args.name}.yaml")
    out.write_text(sc.template_yaml(doc, result), encoding="utf-8")
    print(f"\n{len(result['readable'])} readable addresses, {len(doc['points'])} points -> {out}", file=sys.stderr)
    return 0


def _record_points(args):
    from .config import DeviceConfig, load_file
    from .templates import expand_device, load_user_templates

    if bool(args.template) == bool(args.device):
        raise ValueError("give either --template FILE_OR_NAME or -c CONFIG --device NAME")
    if args.device:
        if not args.config:
            raise ValueError("--device needs -c CONFIG")
        _load_templates(args.templates_dir)
        dev = next((d for d in load_file(args.config).devices if d.name == args.device), None)
        if dev is None:
            raise ValueError(f"device {args.device!r} not found in {args.config}")
    else:
        path = Path(args.template)
        if path.suffix.lower() in (".yaml", ".yml") and path.is_file():
            import yaml

            errs = load_user_templates(path.parent)
            mine = [e for e in errs if Path(e["path"]).resolve() == path.resolve()]
            if mine:
                raise ValueError(f"{path}: {mine[0]['error']}")
            name = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("name") or path.stem
        else:
            _load_templates(args.templates_dir)
            name = args.template
        dev = DeviceConfig(name="record", unit_id=1, template=str(name))
    return expand_device(dev), dev.byte_order, dev.word_order


def _record(argv) -> int:
    import asyncio

    from . import scan as sc

    ap = argparse.ArgumentParser(prog=f"{APP_NAME} record",
                                 description="read-only polling of a real device into a replay CSV")
    _target_args(ap)
    ap.add_argument("--template", help="template YAML file (e.g. from scan) or template name")
    ap.add_argument("-c", "--config", help="config file holding --device")
    ap.add_argument("--device", help="device of --config whose points are recorded")
    ap.add_argument("--templates-dir", help="user template directory")
    ap.add_argument("--period", type=float, default=1.0, help="seconds between polls (default 1)")
    ap.add_argument("--duration", type=float, default=60.0, help="seconds to record (default 60)")
    ap.add_argument("-o", "--output", default="record.csv", help="output CSV (default record.csv)")
    args = ap.parse_args(argv)
    try:
        points, bo, wo = _record_points(args)
        points = sc.record_points(points)
        client = sc.make_client(_target(args), timeout=args.timeout, rate=args.rate)
        plan = sc.record_plan(client.endpoint, sc.check_unit(args.unit), points, args.period, args.duration,
                              args.rate)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rc = _confirm(plan, args.yes, [f"points: {len(points)}, every {args.period:g} s for {args.duration:g} s "
                                   f"-> {args.output}"])
    if rc:
        return rc

    def progress(p):
        _progress(f"row {p['rows']}/{p['cycles']}, failed reads {p['failed']}")

    async def run():
        async with client:
            return await sc.record(client, args.unit, points, args.period, args.duration, Path(args.output),
                                   byte_order=bo, word_order=wo, progress=progress)

    try:
        res = asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        print(f"\nrecord failed: {e}", file=sys.stderr)
        return 1
    print(f"\n{res['rows']} rows -> {res['path']}", file=sys.stderr)
    for w in res["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    rest = sys.argv[1:] if argv is None else list(argv)
    if rest[:1] == ["scan"]:
        return _scan(rest[1:])
    if rest[:1] == ["record"]:
        return _record(rest[1:])
    if rest[:1] == ["validate"]:
        return _validate(rest[1:])
    if rest[:1] == ["templates"]:
        return _templates(rest[1:])
    if rest[:1] == ["run"]:
        rest = rest[1:]
    ap = argparse.ArgumentParser(prog=APP_NAME,
                                 description="Modbus TCP / RTU over TCP device simulator with a web UI "
                                             "(subcommands: run, validate [-c] CONFIG, templates, scan, record); "
                                             "default config: the bundled configs/demo-site.yaml "
                                             "(Windows exe: ~/.sme-modbus-sim/config.yaml, created from it)")
    add_common_args(ap, default_http_port=DEFAULT_HTTP_PORT, app_name=APP_NAME, version=__version__)
    args = ap.parse_args(rest)

    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .config import SimConfig, load_file
    from .engine import Engine

    _load_templates(args.templates_dir)  # before the config: devices may use user templates
    path = args.config
    if path is None:
        default = default_config_path()
        path = str(default) if default else None
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

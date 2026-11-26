"""Regenerate the README / docs screenshots.

Starts the simulator on the demo config (slightly tuned so the curves move), drives it with a small
Modbus client so the protocol trace has traffic, drives the web UI with Playwright and writes PNGs
(plus one animated GIF) to docs/assets/screenshots/<lang>/.

Dev-only tool, not a runtime dependency:

    pip install playwright pillow
    python -m playwright install chromium
    python scripts/screenshots.py            # all languages
    python scripts/screenshots.py --lang en  # one language
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import yaml
from PIL import Image
from playwright.sync_api import Page, sync_playwright
from pymodbus.client import ModbusTcpClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "screenshots"
LANGS = ("en", "ja", "zh")
VIEWPORT = {"width": 1440, "height": 860}
STORAGE = "sme-modbus-sim"  # data-storage prefix of static/index.html


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Loopback binds only: the UI shows listener addresses, and screenshots must not show a LAN address.
HOST = "127.0.0.1"
HTTP_PORT = free_port()
TCP_PORT = free_port()
GW_PORT = free_port()


def demo_config(tmp: Path) -> Path:
    """The demo site on loopback ports, with a faster clock so SOC / power / temperature move."""
    cfg = yaml.safe_load((ROOT / "configs" / "demo-site.yaml").read_text(encoding="utf-8"))
    cfg["simulation"].update({"tick_seconds": 0.5, "time_scale": 60})
    ports = {"tcp1": TCP_PORT, "gw1": GW_PORT}
    for lsn in cfg["listeners"]:
        lsn.update(host=HOST, port=ports[lsn["id"]])
    path = tmp / "demo-site.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (tmp / "templates").mkdir(exist_ok=True)
    return path


def start_sim(config: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        # relative config / template paths: the UI shows them, and a temp path would leak the user name
        [sys.executable, "-m", "sme_modbus_sim", "-c", config.name, "--host", HOST,
         "--http-port", str(HTTP_PORT), "--templates-dir", "templates", "--no-browser"],
        cwd=config.parent, env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://{HOST}:{HTTP_PORT}/", timeout=1)
            return proc
        except OSError:
            if proc.poll() is not None:
                raise SystemExit("simulator exited during startup")
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("simulator did not come up")


def api(method: str, path: str, body=None):
    req = urllib.request.Request(f"http://{HOST}:{HTTP_PORT}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read() or b"null")


def start_battery() -> None:
    """The demo's own recipe: PCS-1 p_setpoint = 20.0 kW discharge, run_command on."""
    c = ModbusTcpClient(HOST, port=TCP_PORT, timeout=2)
    c.connect()
    c.write_register(0, 200, device_id=1)
    c.write_coil(0, True, device_id=1)
    c.close()


def poller(stop: threading.Event) -> None:
    """A SCADA-like master polling PCS / BMS / meter so traffic, statistics and fault hits have content."""
    c = ModbusTcpClient(HOST, port=TCP_PORT, timeout=1)
    while not stop.is_set():
        try:
            if not c.connected:
                c.connect()
            c.read_input_registers(0, count=8, device_id=1)
            c.read_input_registers(0, count=4, device_id=2)
            c.read_input_registers(0, count=16, device_id=3)
            c.read_coils(0, count=2, device_id=1)
        except Exception:  # noqa: BLE001 - injected faults make some requests fail on purpose
            c.close()
        stop.wait(1.0)
    c.close()


def inject_faults() -> None:
    """Two rules so the fault list shows active rules with hit counters."""
    api("POST", "/api/faults", {"action": "exception", "params": {"code": 4}, "unit": 3,
                                "name": "Meter-1: device failure"})
    api("POST", "/api/faults", {"action": "delay", "params": {"ms": 300}, "unit": 2,
                                "name": "BMS-1: slow response"})


def open_ui(page: Page, lang: str, theme: str = "light") -> None:
    page.add_init_script(
        f"localStorage.setItem('{STORAGE}.lang', '{lang}');"
        f"localStorage.setItem('{STORAGE}.theme', '{theme}');")
    page.goto(f"http://{HOST}:{HTTP_PORT}/")
    page.wait_for_selector("#devList li")
    page.wait_for_timeout(800)


def select(page: Page, device: str) -> None:
    page.evaluate(f"selectDevice({json.dumps(device)})")
    page.wait_for_timeout(600)


def trend(page: Page, point: str) -> None:
    page.locator(f"#points tr[data-k='{point}']").first.click()
    page.wait_for_timeout(1500)


def tab(page: Page, name: str) -> None:
    page.evaluate(f"showTab('{name}')")
    page.wait_for_timeout(600)


def close_dialogs(page: Page) -> None:
    page.evaluate("document.querySelectorAll('dialog[open]').forEach(d => d.close())")


LEAKS = re.compile(r"\b(?!127\.)(?:\d{1,3}\.){3}\d{1,3}\b|[A-Za-z]:\\Users\\|/home/|/Users/")


def check_leaks(page: Page, name: str) -> None:
    """Warn when the page shows a LAN address or an absolute user path (text and input values)."""
    text = page.evaluate("""() => document.body.innerText + ' ' +
        [...document.querySelectorAll('input, textarea, select')].map(e => e.value).join(' ')""")
    for m in sorted(set(LEAKS.findall(text))):
        print(f"WARNING {name}: shows {m!r}")


def shoot(page: Page, lang: str, name: str, locator=None) -> None:
    check_leaks(page, f"{lang}/{name}")
    path = OUT / lang / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    (locator or page).screenshot(path=str(path))
    print("wrote", path.relative_to(ROOT))


def gif(page: Page, lang: str, name: str, frames: int = 16, interval_ms: int = 500) -> None:
    """Live-values animation: the register table and trend chart updating, downscaled to keep the file small."""
    images = []
    for _ in range(frames):
        img = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        img = img.resize((img.width * 2 // 3, img.height * 2 // 3), Image.LANCZOS)
        images.append(img.quantize(colors=128, method=Image.MEDIANCUT))
        page.wait_for_timeout(interval_ms)
    path = OUT / lang / f"{name}.gif"
    images[0].save(path, save_all=True, append_images=images[1:], duration=interval_ms, loop=0, optimize=True)
    print("wrote", path.relative_to(ROOT), f"{path.stat().st_size // 1024} KiB")


def overview(page: Page) -> None:
    select(page, "BMS-1")
    trend(page, "soc")
    tab(page, "traffic")


def run_lang(browser, lang: str) -> None:
    page = browser.new_page(viewport=VIEWPORT)
    open_ui(page, lang)

    # 1. overview: device list, live registers, SOC trend of the discharging battery, protocol trace
    overview(page)
    shoot(page, lang, "overview")
    gif(page, lang, "live")

    # 2. meter with an injected fault: register table, power trend, fault list + new-rule form
    select(page, "Meter-1")
    trend(page, "active_power")
    tab(page, "faults")
    page.evaluate("openFaultForm()")
    page.wait_for_timeout(500)
    shoot(page, lang, "faults")

    # 3. per-listener statistics
    select(page, "PCS-1")
    trend(page, "dc_voltage")
    tab(page, "stats")
    page.wait_for_timeout(1000)
    shoot(page, lang, "stats")

    # 4. add device from a template
    page.evaluate("openAddDevice()")
    page.wait_for_selector("#dlgDevice[open]")
    page.wait_for_timeout(600)
    shoot(page, lang, "add-device")
    close_dialogs(page)

    # 5. scan a real device
    page.evaluate("openScan()")
    page.wait_for_selector("#dlgScan[open]")
    page.wait_for_timeout(400)
    shoot(page, lang, "scan")
    close_dialogs(page)
    page.close()

    if lang == "ja":  # wide shot for the company website's open-source page
        page = browser.new_page(viewport={"width": 1600, "height": 960})
        open_ui(page, lang)
        overview(page)
        shoot(page, lang, "overview-wide")
        page.close()

    if lang == "en":
        page = browser.new_page(viewport=VIEWPORT, color_scheme="dark")
        open_ui(page, lang, theme="dark")
        overview(page)
        shoot(page, lang, "overview-dark")
        page.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", choices=LANGS, action="append")
    ap.add_argument("--warmup", type=float, default=20, help="seconds of simulation before the first shot")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        proc = start_sim(demo_config(Path(tmp)))
        stop = threading.Event()
        try:
            start_battery()
            threading.Thread(target=poller, args=(stop,), daemon=True).start()
            inject_faults()
            time.sleep(args.warmup)
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                for lang in args.lang or LANGS:
                    run_lang(browser, lang)
                browser.close()
        finally:
            stop.set()
            proc.terminate()
            proc.wait(10)


if __name__ == "__main__":
    main()

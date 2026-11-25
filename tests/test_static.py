"""Static web UI: served by the app (product files under /static, shared shell under /sme-sim-common) and every
i18n key it uses exists. Completeness of en / ja / zh and the reserved prefixes are checked in test_i18n.py."""

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sme_sim_common import __version__ as common_version
from sme_sim_common.testing import iter_i18n_keys
from sme_sim_common.web import STATIC_DIR as COMMON_STATIC, VERSION_TOKEN

from sme_modbus_sim.config import parse_text
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.web import create_app

STATIC = Path(__file__).resolve().parents[1] / "sme_modbus_sim" / "static"
RESERVED = ("shell.", "vendor.", "common.")

CONFIG = """
listeners:
  - {id: tcp1, type: tcp, host: 127.0.0.1, port: 15030}
devices:
  - name: D
    unit_id: 1
    points:
      - {name: sp, table: holding, address: 0, datatype: int16}
"""


def _en_keys(*paths: Path) -> set:
    return {k.key for p in paths for k in iter_i18n_keys(p) if k.lang == "en"}


def _used_keys() -> set:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-placeholder|-title|-aria-label)?="([^"]+)"', html))
    keys |= set(re.findall(r'\bt\(\s*"([^"]+)"', js))
    keys |= set(re.findall(r'label:\s*"((?:menu|addr|common)\.[^"]+)"', js))
    # keys built from template literals in app.js
    keys |= {f"addr.{m}.short" for m in ("zero", "one", "modicon")} | {f"addr.{m}" for m in ("zero", "one", "modicon")}
    keys |= {f"table.{x}" for x in ("coil", "discrete", "input", "holding")}
    keys |= {f"access.{a}{s}" for a in ("r", "rw") for s in ("", ".title")}
    return keys


def test_index_and_assets_served(tmp_path):
    eng = Engine(parse_text(CONFIG), tmp_path, config_text=CONFIG,
                 listener_overrides={"tcp1": {"host": "127.0.0.1", "port": 0}})
    with TestClient(create_app(eng)) as c:
        r = c.get("/")
        assert r.status_code == 200 and "SME ModbusSim" in r.text and 'data-storage="sme-modbus-sim"' in r.text
        assert VERSION_TOKEN not in r.text and f"?v={common_version}" in r.text
        for asset in ("app.js", "style.css", "i18n/en.js", "i18n/ja.js", "i18n/zh.js", "favicon.svg"):
            assert c.get(f"/static/{asset}").status_code == 200, asset
        for asset in ("shell/shell.js", "shell/shell.css", "vendor/vendor.js", "i18n/common.js",
                      "brand/company-logo.svg"):
            assert c.get(f"/sme-sim-common/{asset}").status_code == 200, asset


def test_used_keys_defined():
    used = _used_keys()
    assert len(used) > 50
    product = _en_keys(STATIC / "i18n" / "en.js")
    common = _en_keys(*sorted(COMMON_STATIC.rglob("*.js")))
    missing = sorted(k for k in used if k not in (common if k.startswith(RESERVED) else product))
    assert not missing, f"missing {missing}"

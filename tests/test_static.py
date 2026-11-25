"""Static web UI: served by the app, and every i18n key it uses exists in en / ja / zh."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sme_modbus_sim.config import parse_text
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.web import create_app

STATIC = Path(__file__).resolve().parents[1] / "sme_modbus_sim" / "static"
KEY = r'"([A-Za-z0-9_.]+)"\s*:'

CONFIG = """
listeners:
  - {id: tcp1, type: tcp, host: 127.0.0.1, port: 15030}
devices:
  - name: D
    unit_id: 1
    points:
      - {name: sp, table: holding, address: 0, datatype: int16}
"""


def _dict(path: Path, lang: str) -> set:
    text = path.read_text(encoding="utf-8")
    m = re.search(r'Shell\.addStrings\("%s",\s*\{(.*?)\n\}\);' % lang, text, re.S)
    assert m, f"no Shell.addStrings({lang!r}, ...) block in {path.name}"
    return set(re.findall(KEY, m.group(1)))


@pytest.fixture(scope="module")
def dicts():
    return {"en": _dict(STATIC / "app.js", "en"),
            "ja": _dict(STATIC / "i18n" / "ja.js", "ja"),
            "zh": _dict(STATIC / "i18n" / "zh.js", "zh")}


def _used_keys() -> set:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-placeholder|-title|-aria-label)?="([^"]+)"', html))
    keys |= set(re.findall(r'\bt\(\s*"([^"]+)"', js))
    keys |= set(re.findall(r'label:\s*"((?:menu|addr)\.[^"]+)"', js))
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
        for asset in ("app.js", "style.css", "shell/shell.js", "shell/shell.css", "i18n/ja.js", "i18n/zh.js",
                      "favicon.svg", "brand/company-logo.svg"):
            assert c.get(f"/static/{asset}").status_code == 200, asset


def test_used_keys_defined_everywhere(dicts):
    used = _used_keys()
    assert len(used) > 50
    for lang, d in dicts.items():
        missing = sorted(used - d)
        assert not missing, f"{lang}: missing {missing}"


def test_translations_complete(dicts):
    for lang in ("ja", "zh"):
        assert not sorted(dicts["en"] - dicts[lang]), f"{lang} lacks keys"
        assert not sorted(dicts[lang] - dicts["en"]), f"{lang} has keys unknown to en"
    assert {k for k in dicts["en"] if k.startswith("vendor.")} == {
        "vendor.blurb", "vendor.svc.ems", "vendor.svc.market", "vendor.svc.edge",
        "vendor.svc.integration", "vendor.svc.ai", "vendor.svc.cloud"}


def test_vendored_headers():
    for rel in ("shell/shell.js", "shell/shell.css", "favicon.svg", "brand/company-logo.svg"):
        assert "Vendored from SME BACnetSim (ebd27ec)" in (STATIC / rel).read_text(encoding="utf-8")[:300], rel

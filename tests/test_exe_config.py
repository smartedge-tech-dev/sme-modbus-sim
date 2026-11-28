"""Default config location of the Windows executable.

A PyInstaller onefile exe runs from a temporary extraction directory (``sys._MEIPASS``) that is deleted on exit,
so started without ``-c`` it uses the per-user ``~/.sme-modbus-sim/config.yaml`` (created from the bundled demo
config on the first run) and never writes below ``sys._MEIPASS``. Frozen mode is faked here."""

import logging
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sme_modbus_sim.__main__ as cli
import sme_modbus_sim.web as web
from sme_modbus_sim import templates

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "configs" / "demo-site.yaml"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.delenv("SME_MODBUS_SIM_TEMPLATES", raising=False)
    return h


@pytest.fixture
def frozen(tmp_path, monkeypatch, home):
    """Fake PyInstaller onefile: ``sys.frozen``, ``sys._MEIPASS`` and the bundled demo config inside it."""
    meipass = tmp_path / "_MEI12345"
    (meipass / "configs").mkdir(parents=True)
    bundled = meipass / "configs" / "demo-site.yaml"
    shutil.copyfile(DEMO, bundled)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(cli, "DEFAULT_CONFIG", bundled)
    return meipass


def _start(monkeypatch, argv):
    """Run ``main`` without the web server; return the engine and the config path given to the web app."""
    seen = {}
    monkeypatch.setattr(web, "create_app", lambda engine, path: seen.update(engine=engine, path=path) or "app")
    monkeypatch.setattr(cli, "run_web", lambda app, args, name: None)
    assert cli.main(list(argv) + ["--no-browser"]) == 0
    return seen["engine"], seen["path"]


def _snapshot(d: Path):
    return {p.relative_to(d): p.read_bytes() for p in d.rglob("*") if p.is_file()}


def test_user_data_dir_is_shared_with_templates(home):
    assert cli.user_data_dir() == home / ".sme-modbus-sim"
    assert templates.default_templates_dir() == home / ".sme-modbus-sim" / "templates"


def test_first_run_copies_bundled_config(frozen, home, monkeypatch, caplog):
    user = home / ".sme-modbus-sim" / "config.yaml"
    assert not user.exists()
    with caplog.at_level(logging.INFO, logger="sme-modbus-sim"):
        engine, path = _start(monkeypatch, [])
    assert path == str(user)
    assert user.read_bytes() == DEMO.read_bytes()
    assert Path(engine.config_path) == user and engine.config.devices
    assert Path(engine.base_dir) == user.parent  # recordings land next to the user config
    assert "created" in caplog.text


def test_existing_user_config_is_kept(frozen, home, monkeypatch):
    user = home / ".sme-modbus-sim" / "config.yaml"
    user.parent.mkdir(parents=True)
    user.write_text("devices: []\n", encoding="utf-8")
    engine, path = _start(monkeypatch, [])
    assert path == str(user)
    assert user.read_text(encoding="utf-8") == "devices: []\n"
    assert engine.config.devices == []


def test_save_writes_user_config_not_meipass(frozen, home, monkeypatch):
    real_create_app = web.create_app
    engine, path = _start(monkeypatch, [])
    before = _snapshot(frozen)
    user = home / ".sme-modbus-sim" / "config.yaml"
    user.write_text("# stale\n", encoding="utf-8")
    monkeypatch.setattr(web, "create_app", real_create_app)
    with TestClient(real_create_app(engine, path)) as c:
        r = c.post("/api/config/save")
    assert r.status_code == 200, r.text
    text = user.read_text(encoding="utf-8")
    assert "devices:" in text and "# stale" not in text
    assert _snapshot(frozen) == before, "nothing may be written below sys._MEIPASS"


def test_explicit_config_used_in_place(frozen, home, monkeypatch, tmp_path):
    mine = tmp_path / "my-site.yaml"
    shutil.copyfile(DEMO, mine)
    engine, path = _start(monkeypatch, ["-c", str(mine)])
    assert path == str(mine)
    assert Path(engine.config_path) == mine
    assert not (home / ".sme-modbus-sim" / "config.yaml").exists(), "no user config is created when -c is given"


def test_not_frozen_uses_bundled_demo(home, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    _, path = _start(monkeypatch, [])
    assert Path(path) == cli.DEFAULT_CONFIG
    assert not (home / ".sme-modbus-sim").exists()

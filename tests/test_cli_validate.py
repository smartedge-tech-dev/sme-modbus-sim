"""``sme-modbus-sim validate``: positional or ``-c`` config, exit 2 on errors caught before the simulator runs."""
from pathlib import Path

import pytest

from sme_modbus_sim.__main__ import main

DEMO = Path(__file__).resolve().parents[1] / "configs" / "demo-site.yaml"


def _cfg(tmp_path: Path, expr: str) -> Path:
    p = tmp_path / "sim.yaml"
    p.write_text(f"""
listeners:
  - {{id: tcp1, type: tcp, port: 5020}}
devices:
  - name: d
    unit_id: 1
    points:
      - {{name: v, table: holding, address: 0, datatype: uint16}}
      - name: w
        table: holding
        address: 1
        datatype: uint16
        behavior: {{type: expr, expr: "{expr}"}}
""", encoding="utf-8")
    return p


@pytest.mark.parametrize("argv", [["validate", str(DEMO)], ["validate", "-c", str(DEMO)],
                                  ["validate", "--config", str(DEMO)]])
def test_validate_positional_or_option(argv, capsys):
    assert main(argv) == 0
    assert "OK" in capsys.readouterr().out


def test_validate_without_config_is_a_usage_error():
    with pytest.raises(SystemExit) as e:
        main(["validate"])
    assert e.value.code == 2


def test_validate_accepts_a_valid_expr(tmp_path):
    assert main(["validate", str(_cfg(tmp_path, "v + pv('d', 'v')"))]) == 0


@pytest.mark.parametrize("expr, word", [("pv > 3", "pv"), ("max + 1", "max"), ("t(1)", "'t'")])
def test_validate_rejects_function_value_misuse(tmp_path, capsys, expr, word):
    # These used to pass validate and then raise TypeError on every tick of a running simulator.
    assert main(["validate", "-c", str(_cfg(tmp_path, expr))]) == 2
    assert word in capsys.readouterr().err

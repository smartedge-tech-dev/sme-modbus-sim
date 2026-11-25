"""Excel (.xlsx) point table: import / export / blank template (design §5.6).

The sheet framework (``#`` description rows, ``extra`` YAML column, typed column converters, row-level error
mapping) is ``sme_sim_common.xlsx.XlsxSchema``; this module supplies the Modbus columns, the Modicon address
converter and the config <-> rows hooks. ``export_xlsx`` returns ``(bytes, warnings)``: a cell longer than the
Excel limit is left out with a warning.

Workbook layout (row 1: English column names; row 2 starts with ``#`` and carries a Japanese / Chinese
description; any row whose first non-empty cell starts with ``#`` is ignored on import):

* ``Listeners``: one row per listener. Fields without a column go into ``extra`` (YAML flow mapping), so
  listener types / fields added later (serial ``rtu``) round-trip without a code change here.
* ``Devices``: one row per device (unit).
* ``Points``: one row per point. For a device with a ``template`` the rows are the template overrides /
  additions, exactly as ``points:`` in YAML; ``table`` may then be left empty for an override.

The ``simulation`` section has no sheet: an import keeps the settings of the running configuration.

Address column: a **number** is the 0-based protocol address (0..65535). Modicon notation is recognised for
**text** cells of 5 or 6 digits whose first digit is the table prefix (``0`` coil, ``1`` discrete input,
``3`` input register, ``4`` holding register): ``'40001`` = holding 0, ``'400001`` = holding 0 (6 digits reach
65536). A numeric cell above 65535 with 6 digits (``400001``) is also read as Modicon. The prefix sets the
table; a different value in the ``table`` column is an error. Excel turns ``40001`` typed into a cell into a
number, so prefix it with an apostrophe (``'40001``) or format the column as text.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError
from sme_sim_common.xlsx import CELL_LIMIT, Sheet, SheetRow, XlsxSchema, pydantic_errors, to_yaml  # noqa: F401

from .config import MAX_ADDRESS, DeviceConfig, ListenerConfig, PointConfig, SimConfig
from .templates import expand_device

TEMPLATE_FILE = "sme-modbus-sim-points-template.xlsx"
EXPORT_FILE = "sme-modbus-sim-points.xlsx"
MODICON = {"0": "coil", "1": "discrete", "3": "input", "4": "holding"}

_EXTRA = ("yaml", "列のない設定（YAML マッピング）/ 无对应列的设置（YAML 映射）")
# column -> (kind, description "ja / zh")
LISTENER_COLUMNS: Dict[str, Tuple[str, str]] = {
    "id": ("str", "リスナー ID（必須）/ 监听器 ID（必填）"),
    "type": ("lower", "tcp / rtu_over_tcp / rtu（シリアル）/ tcp / rtu_over_tcp / rtu（串口）"),
    "host": ("str", "待受アドレス 例 0.0.0.0 / 监听地址，例 0.0.0.0"),
    "port": ("port", "TCP ポート番号またはシリアルポート名 例 5020 / COM5 / TCP 端口号或串口名"),
    "unit_id_mode": ("lower", "strict / any"),
    "unknown_unit": ("lower", "ignore / exception"),
    "default_unit": ("int", "既定ユニット ID（TCP の 0 / 255 用）/ 默认 unit ID（TCP 的 0 / 255）"),
    "baudrate": ("int", "シリアル: ボーレート / 串口：波特率"),
    "bytesize": ("int", "シリアル: データビット / 串口：数据位"),
    "parity": ("str", "シリアル: パリティ N / E / O / 串口：校验 N / E / O"),
    "stopbits": ("number", "シリアル: ストップビット 1 / 2 / 串口：停止位 1 / 2"),
    "extra": _EXTRA,
}
DEVICE_COLUMNS: Dict[str, Tuple[str, str]] = {
    "name": ("str", "デバイス名（必須・一意）/ 设备名（必填，唯一）"),
    "description": ("str", "説明 / 描述"),
    "unit_id": ("int", "ユニット ID 1..247（必須）/ unit ID 1..247（必填）"),
    "listeners": ("list", "リスナー ID（カンマ区切り、空欄＝全て）/ 监听器 ID（逗号分隔，空＝全部）"),
    "template": ("str", "テンプレート名（空欄＝なし）/ 模板名（空＝不用模板）"),
    "params": ("yaml", "テンプレートパラメータ YAML 例 {rated_kw: 50} / 模板参数 YAML"),
    "byte_order": ("lower", "big / little（既定 big）/ big / little（默认 big）"),
    "word_order": ("lower", "big / little（既定 big）/ big / little（默认 big）"),
    "undefined_address": ("lower", "exception / zero"),
    "identification": ("yaml", "識別情報 YAML 例 {vendor_name: SME} / 设备标识 YAML"),
    "extra": _EXTRA,
}
POINT_COLUMNS: Dict[str, Tuple[str, str]] = {
    "device": ("str", "所属デバイス名（必須）/ 所属设备名（必填）"),
    "table": ("lower", "coil / discrete / input / holding（テンプレート上書きは空欄可）/（模板覆盖可空）"),
    "address": ("address", "0 始まりの数値、または文字列 '40001 形式 / 从 0 开始的数字，或文本 '40001 形式"),
    "name": ("str", "ポイント名（必須・デバイス内で一意）/ 点名（必填，设备内唯一）"),
    "description": ("str", "説明 / 描述"),
    "datatype": ("lower", "bool / bit / uint16 / int16 / uint32 / int32 / float32 / uint64 / int64 / float64 / "
                          "string / enum"),
    "count": ("int", "string のレジスタ数 / string 的寄存器数"),
    "bit": ("int", "bit 型のビット位置 0..15 / bit 类型的位 0..15"),
    "byte_order": ("lower", "big / little（空欄＝デバイス既定）/（空＝设备默认）"),
    "word_order": ("lower", "big / little（空欄＝デバイス既定）/（空＝设备默认）"),
    "scale": ("float", "工学値 = raw × scale + offset / 工程值 = raw × scale + offset"),
    "offset": ("float", "オフセット / 偏移"),
    "units": ("str", "単位 例 kW / 单位，例 kW"),
    "access": ("lower", "r / rw"),
    "value": ("value", "初期値（工学値）/ 初始值（工程值）"),
    "min": ("float", "最小値 / 最小值"),
    "max": ("float", "最大値 / 最大值"),
    "labels": ("yaml", "enum ラベル YAML 例 {0: Stopped, 2: Running} / enum 标签 YAML"),
    "behavior": ("yaml", "挙動 YAML 例 {type: sine, min: 20, max: 26} / 行为 YAML"),
    "extra": _EXTRA,
}
SHEETS = {"Listeners": LISTENER_COLUMNS, "Devices": DEVICE_COLUMNS, "Points": POINT_COLUMNS}


# ---------------------------------------------------------------- Modicon address column

def parse_address(v: Any) -> Tuple[int, Optional[str]]:
    """Address cell -> (0-based address, table implied by Modicon notation or None). See module docstring."""
    if isinstance(v, bool):
        raise ValueError(f"not an address: {v!r}")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        if 0 <= v <= MAX_ADDRESS:
            return v, None
        s = str(v)
        if not (len(s) == 6 and s[0] in MODICON):
            raise ValueError(f"address {v} out of range 0..{MAX_ADDRESS}")
    elif isinstance(v, str):
        s = v.strip()
        if not s.isdigit():
            raise ValueError(f"not an address: {v!r} (0-based number or Modicon text such as '40001')")
        if not (len(s) in (5, 6) and s[0] in MODICON):
            n = int(s)
            if n > MAX_ADDRESS:
                raise ValueError(f"address {n} out of range 0..{MAX_ADDRESS}")
            return n, None
    else:
        raise ValueError(f"not an address: {v!r}")
    off, top = int(s[1:]), (9999 if len(s) == 5 else MAX_ADDRESS + 1)
    if not 1 <= off <= top:
        raise ValueError(f"Modicon address {s}: register number must be 1..{top}")
    return off - 1, MODICON[s[0]]


REQUIRED = {"Listeners": ("id",), "Devices": ("name", "unit_id"), "Points": ("device", "name")}
LISTENERS, DEVICES, POINTS = "Listeners", "Devices", "Points"


def _sheet_of(title: str) -> Sheet:
    cols = SHEETS[title]
    kinds = {c: (parse_address if k == "address" else k) for c, (k, _) in cols.items()}
    return Sheet(title, [(c, d) for c, (_, d) in cols.items()], kinds, REQUIRED[title], optional=title == LISTENERS)


def _export_rows(cfg: SimConfig) -> Dict[str, List[Dict[str, Any]]]:
    """Listeners, devices and points (template devices: their overrides only) as model dumps per sheet."""
    rows: Dict[str, List[Dict[str, Any]]] = {t: [] for t in SHEETS}
    for l in cfg.listeners:
        d = l.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        rows[LISTENERS].append({"id": l.id, **d})
    for dev in cfg.devices:
        d = dev.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        d.pop("points", None)
        rows[DEVICES].append({"name": dev.name, "unit_id": dev.unit_id, **d})
        for p in dev.points:
            pd = p.model_dump(mode="json", exclude_unset=True, exclude_none=True)
            if "labels" in pd:
                pd["labels"] = {int(k): v for k, v in pd["labels"].items()}
            rows[POINTS].append({"device": dev.name, **pd})
    return rows


def _build(tables: Dict[str, List[SheetRow]], base: Optional[SimConfig], errors: List[str],
           warnings: List[str]) -> Optional[SimConfig]:
    """Rows per sheet -> ``SimConfig``; every error names the sheet, row and (where known) column."""
    base = base or SimConfig()

    # listeners
    listeners: List[Dict[str, Any]] = []
    lsn_rows: Dict[str, int] = {}
    for r in tables[LISTENERS]:
        if not r.ok:
            continue
        l = r.merged()
        if l["id"] in lsn_rows:
            errors.append(f"Listeners row {r.row_no}, column 'id': duplicate id {l['id']!r} "
                          f"(also row {lsn_rows[l['id']]})")
            continue
        try:
            ListenerConfig.model_validate(l)
        except ValidationError as e:
            errors.extend(pydantic_errors(e, f"Listeners row {r.row_no}", list(LISTENER_COLUMNS)))
            continue
        lsn_rows[l["id"]] = r.row_no
        listeners.append(l)

    # devices
    devices: Dict[str, Dict[str, Any]] = {}
    dev_rows: Dict[str, int] = {}
    for r in tables[DEVICES]:
        d = r.merged()
        if "name" not in r.values:
            continue  # a row with other errors is still registered, so its Points rows add no follow-up errors
        if d["name"] in devices:
            errors.append(f"Devices row {r.row_no}, column 'name': duplicate device {d['name']!r} "
                          f"(also row {dev_rows[d['name']]})")
            continue
        devices[d["name"]] = {**d, "points": []}
        dev_rows[d["name"]] = r.row_no

    # points
    point_rows: Dict[Tuple[str, str], int] = {}
    for r in tables[POINTS]:
        n = len(errors)
        p = dict(r.values)
        prefix = f"Points row {r.row_no}"
        if not {"device", "name"} <= p.keys():
            continue  # required / conversion error already reported
        dev = devices.get(p["device"])
        if dev is None:
            errors.append(f"{prefix}, column 'device': device {p['device']!r} is not in the Devices sheet")
            continue
        key = (p["device"], p["name"])
        if key in point_rows:
            errors.append(f"{prefix}, column 'name': duplicate point {p['name']!r} in device {p['device']!r} "
                          f"(also row {point_rows[key]})")
            continue
        if "address" in p:
            addr, table = p["address"]
            p["address"] = addr
            if table and p.setdefault("table", table) != table:
                errors.append(f"{prefix}, column 'address': Modicon address {r.raw['address']} is a {table} "
                              f"address but table is {p['table']!r}")
        if not r.ok or len(errors) > n:
            continue
        o = {**p.pop("extra", {}), **{k: v for k, v in p.items() if k != "device"}}
        try:
            PointConfig.model_validate(o)
        except ValidationError as e:
            errors.extend(pydantic_errors(e, prefix, list(POINT_COLUMNS)))
            continue
        point_rows[key] = r.row_no
        dev["points"].append(o)

    if errors:
        return None

    def locate(msg: str) -> str:
        """Best-effort sheet / row for a cross-row validation message."""
        for (dname, pname), row in point_rows.items():
            if f"{dname}/{pname}:" in msg:
                return f"Points row {row}"
        for dname, row in dev_rows.items():
            if f"'{dname}'" in msg or msg.startswith(f"{dname}:") or msg.startswith(f"{dname}/"):
                return f"Devices row {row}"
        for lid, row in lsn_rows.items():
            if f"'{lid}'" in msg:
                return f"Listeners row {row}"
        return "workbook"

    for name, d in devices.items():
        try:
            expand_device(DeviceConfig.model_validate(d))
        except ValidationError as e:
            errors.extend(pydantic_errors(e, f"Devices row {dev_rows[name]}", list(DEVICE_COLUMNS)))
        except ValueError as e:
            errors.append(f"{locate(str(e))}: {e}")
    if errors:
        return None

    top: Dict[str, Any] = {"listeners": listeners or [l.model_dump(mode="json", exclude_unset=True,
                                                                    exclude_none=True) | {"id": l.id}
                                                       for l in base.listeners],
                           "devices": list(devices.values())}
    if "simulation" in base.model_fields_set:
        top = {"simulation": base.simulation.model_dump(mode="json", exclude_unset=True), **top}
    try:
        return SimConfig.model_validate(top)
    except ValidationError as e:
        errors.extend(f"{locate(str(err['msg']))}: {str(err['msg']).removeprefix('Value error, ')}"
                      for err in e.errors())
        return None


SCHEMA = XlsxSchema([_sheet_of(t) for t in SHEETS], export_rows=_export_rows, build=_build,
                    template_file=TEMPLATE_FILE, export_file=EXPORT_FILE)


def blank_template() -> bytes:
    """An empty workbook with all sheets, column names and description rows."""
    return SCHEMA.blank_template()


def export_xlsx(cfg: SimConfig) -> Tuple[bytes, List[str]]:
    """Export listeners, devices and points as a workbook: ``(bytes, warnings)`` (over-long cells left out)."""
    return SCHEMA.export_xlsx(cfg)


def import_xlsx(data: bytes, base: Optional[SimConfig] = None) -> Tuple[Optional[SimConfig], List[str]]:
    """Parse a point-table workbook.

    Returns ``(config, warnings)`` on success and ``(None, errors)`` on failure; every error names the sheet,
    row and (where known) column. ``base`` supplies the simulation settings, and the listeners when the
    ``Listeners`` sheet is missing or empty.
    """
    return SCHEMA.import_xlsx(data, base)

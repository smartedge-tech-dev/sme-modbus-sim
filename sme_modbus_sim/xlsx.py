"""Excel (.xlsx) point table: import / export / blank template (design §5.6).

Adapted from SME BACnetSim (ebd27ec) ``sme_bacnet_sim/xlsx.py``: sheet framework, ``#`` description rows,
``extra`` YAML column, typed column converters and row-level error mapping.

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

import io
from typing import Any, Dict, List, Optional, Tuple

import yaml
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from pydantic import ValidationError

from .config import MAX_ADDRESS, DeviceConfig, ListenerConfig, PointConfig, SimConfig
from .templates import expand_device

TEMPLATE_FILE = "sme-modbus-sim-points-template.xlsx"
EXPORT_FILE = "sme-modbus-sim-points.xlsx"
CELL_LIMIT = 32767  # Excel's maximum characters per cell
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
_YAML_COLS = {"params", "identification", "labels", "behavior"}


# ---------------------------------------------------------------- helpers

def _flow(v: Any) -> str:
    return yaml.safe_dump(v, default_flow_style=True, sort_keys=False, allow_unicode=True, width=10 ** 9).strip()


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (bool, int, float, str))


def to_yaml(cfg: SimConfig) -> str:
    """YAML text of a config with only the fields that were set (keeps template overrides minimal)."""
    return yaml.safe_dump(cfg.model_dump(mode="json", exclude_unset=True, exclude_none=True),
                          allow_unicode=True, sort_keys=False)


def _sheet(wb: Workbook, title: str, columns: Dict[str, Tuple[str, str]], first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(list(columns))
    ws.append(["# " + d if i == 0 else d for i, (_, d) in enumerate(columns.values())])
    bold, grey = Font(bold=True), Font(italic=True, color="666666")
    fill = PatternFill("solid", fgColor="DDEBF7")
    for i, name in enumerate(columns, 1):
        ws.cell(1, i).font, ws.cell(1, i).fill = bold, fill
        ws.cell(2, i).font = grey
        ws.column_dimensions[get_column_letter(i)].width = max(12, min(40, len(name) + 6))
    ws.freeze_panes = "A3"
    return ws


def _workbook() -> Workbook:
    wb = Workbook()
    for i, (title, cols) in enumerate(SHEETS.items()):
        _sheet(wb, title, cols, first=i == 0)
    return wb


def _save(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def blank_template() -> bytes:
    """An empty workbook with all sheets, column names and description rows."""
    return _save(_workbook())


# ---------------------------------------------------------------- export

def _row(d: Dict[str, Any], columns: Dict[str, Tuple[str, str]]) -> Dict[str, Any]:
    """Pop the column fields out of a model dump; what is left goes to ``extra``."""
    row: Dict[str, Any] = {}
    for col, (kind, _) in columns.items():
        if col == "extra" or col not in d:
            continue
        v = d[col]
        if kind == "yaml":
            row[col] = _flow(v) if v not in ({}, None) else None
        elif kind == "list":
            if any("," in str(x) for x in v):
                continue  # not representable as a comma list: keep in extra
            row[col] = ",".join(map(str, v))
        elif _scalar(v):
            row[col] = v
        else:
            continue
        d.pop(col)
        if isinstance(row[col], str) and len(row[col]) > CELL_LIMIT:
            raise ValueError(f"{col}: longer than {CELL_LIMIT} characters (Excel cell limit)")
    if d:
        row["extra"] = _flow(d)
    return row


def export_xlsx(cfg: SimConfig) -> bytes:
    """Export listeners, devices and points (template devices: their overrides only) as a workbook."""
    wb = _workbook()
    rows: Dict[str, List[Dict[str, Any]]] = {t: [] for t in SHEETS}
    for l in cfg.listeners:
        d = l.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        rows["Listeners"].append(_row({"id": l.id, **d}, LISTENER_COLUMNS))
    for dev in cfg.devices:
        d = dev.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        d.pop("points", None)
        rows["Devices"].append(_row({"name": dev.name, "unit_id": dev.unit_id, **d}, DEVICE_COLUMNS))
        for p in dev.points:
            pd = p.model_dump(mode="json", exclude_unset=True, exclude_none=True)
            if "labels" in pd:
                pd["labels"] = {int(k): v for k, v in pd["labels"].items()}
            rows["Points"].append(_row({"device": dev.name, **pd}, POINT_COLUMNS))
    for title, cols in SHEETS.items():
        ws = wb[title]
        for r in rows[title]:
            ws.append([r.get(c) for c in cols])
    return _save(wb)


# ---------------------------------------------------------------- import

def _norm(s: Any) -> str:
    return str(s).strip().lower().replace(" ", "_").replace("-", "_") if s is not None else ""


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


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


def _convert(kind: str, v: Any) -> Any:
    """Cell value -> config value for a column kind; raises ValueError with a short reason."""
    if isinstance(v, str):
        v = v.strip()
    if kind == "int":
        if isinstance(v, bool):
            raise ValueError(f"not an integer: {v!r}")
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, int) or (isinstance(v, str) and v.lstrip("-").isdigit()):
            return int(v)
        raise ValueError(f"not an integer: {v!r}")
    if kind in ("float", "number"):
        try:
            if isinstance(v, bool):
                raise ValueError
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"not a number: {v!r}") from None
        return int(f) if kind == "number" and f.is_integer() else f
    if kind == "port":
        return _convert("int", v) if isinstance(v, (int, float)) or str(v).isdigit() else str(v)
    if kind == "yaml":
        try:
            data = yaml.safe_load(str(v))
        except yaml.YAMLError as e:
            raise ValueError(f"invalid YAML: {str(e).splitlines()[0]}") from None
        if not isinstance(data, dict):
            raise ValueError("expected a YAML mapping such as {key: value}")
        return data
    if kind == "list":
        return [x.strip() for x in str(v).split(",") if x.strip()]
    if kind == "address":
        return parse_address(v)
    if kind in ("str", "lower"):
        s = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
        return s.lower() if kind == "lower" else s
    return v  # value: as read


def _table(ws, columns: Dict[str, Tuple[str, str]], errors: List[str], warnings: List[str],
           required: Tuple[str, ...]) -> List[Tuple[int, Dict[str, Any]]]:
    """Read a sheet as (row number, {column: raw value}) for the non-empty, non-comment rows."""
    header: Optional[Dict[int, str]] = None
    rows = []
    for row_no, values in enumerate(ws.iter_rows(values_only=True), 1):
        if all(_blank(v) for v in values):
            continue
        first = next((v for v in values if not _blank(v)), None)
        if isinstance(first, str) and first.lstrip().startswith("#"):
            continue
        if header is None:
            header = {}
            for i, v in enumerate(values):
                name = _norm(v)
                if not name:
                    continue
                if name not in columns:
                    warnings.append(f"{ws.title}: unknown column {v!r} ignored")
                    continue
                header[i] = name
            missing = [c for c in required if c not in header.values()]
            if missing:
                errors.append(f"{ws.title} row {row_no}: missing column(s) {', '.join(missing)}")
                return []
            continue
        rows.append((row_no, {name: values[i] for i, name in header.items()
                              if i < len(values) and not _blank(values[i])}))
    return rows


def _pydantic_errors(e: ValidationError, prefix: str, columns: Dict[str, Any]) -> List[str]:
    out = []
    for err in e.errors():
        loc = [str(x) for x in err["loc"]]
        col = (loc[0] if loc[0] in columns else "extra") if loc else None
        msg = str(err["msg"]).removeprefix("Value error, ")
        out.append(f"{prefix}{', column ' + repr(col) if col else ''}: "
                   f"{'.'.join(loc) + ': ' if loc else ''}{msg}")
    return out


def import_xlsx(data: bytes, base: Optional[SimConfig] = None) -> Tuple[Optional[SimConfig], List[str]]:
    """Parse a point-table workbook.

    Returns ``(config, warnings)`` on success and ``(None, errors)`` on failure; every error names the sheet,
    row and (where known) column. ``base`` supplies the simulation settings, and the listeners when the
    ``Listeners`` sheet is missing or empty.
    """
    base = base or SimConfig()
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:  # not a zip / not a workbook
        return None, [f"not a readable .xlsx file: {e}"]
    errors: List[str] = []
    warnings: List[str] = []
    for title in ("Devices", "Points"):
        if title not in wb.sheetnames:
            errors.append(f"sheet {title!r} is missing")
    if errors:
        return None, errors

    def convert(sheet: str, row_no: int, cols: Dict[str, Tuple[str, str]], raw: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for col, v in raw.items():
            try:
                out[col] = _convert(cols[col][0], v)
            except ValueError as e:
                errors.append(f"{sheet} row {row_no}, column {col!r}: {e}")
        return out

    def required(sheet: str, row_no: int, raw: Dict[str, Any], cols: Tuple[str, ...]) -> bool:
        for col in cols:
            if col not in raw:
                errors.append(f"{sheet} row {row_no}, column {col!r}: required")
        return all(c in raw for c in cols)

    # listeners
    listeners: List[Dict[str, Any]] = []
    lsn_rows: Dict[str, int] = {}
    if "Listeners" in wb.sheetnames:
        for row_no, raw in _table(wb["Listeners"], LISTENER_COLUMNS, errors, warnings, ("id",)):
            n = len(errors)
            l = convert("Listeners", row_no, LISTENER_COLUMNS, raw)
            if not required("Listeners", row_no, raw, ("id",)) or len(errors) > n:
                continue
            l = {**l.pop("extra", {}), **l}
            if l["id"] in lsn_rows:
                errors.append(f"Listeners row {row_no}, column 'id': duplicate id {l['id']!r} "
                              f"(also row {lsn_rows[l['id']]})")
                continue
            try:
                ListenerConfig.model_validate(l)
            except ValidationError as e:
                errors.extend(_pydantic_errors(e, f"Listeners row {row_no}", LISTENER_COLUMNS))
                continue
            lsn_rows[l["id"]] = row_no
            listeners.append(l)

    # devices
    devices: Dict[str, Dict[str, Any]] = {}
    dev_rows: Dict[str, int] = {}
    for row_no, raw in _table(wb["Devices"], DEVICE_COLUMNS, errors, warnings, ("name", "unit_id")):
        d = convert("Devices", row_no, DEVICE_COLUMNS, raw)
        required("Devices", row_no, raw, ("name", "unit_id"))
        if "name" not in d:
            continue  # a row with other errors is still registered, so its Points rows add no follow-up errors
        if d["name"] in devices:
            errors.append(f"Devices row {row_no}, column 'name': duplicate device {d['name']!r} "
                          f"(also row {dev_rows[d['name']]})")
            continue
        devices[d["name"]] = {**d.pop("extra", {}), **d, "points": []}
        dev_rows[d["name"]] = row_no

    # points
    point_rows: Dict[Tuple[str, str], int] = {}
    for row_no, raw in _table(wb["Points"], POINT_COLUMNS, errors, warnings, ("device", "name")):
        n = len(errors)
        p = convert("Points", row_no, POINT_COLUMNS, raw)
        prefix = f"Points row {row_no}"
        if not required("Points", row_no, raw, ("device", "name")) or not {"device", "name"} <= p.keys():
            continue
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
                errors.append(f"{prefix}, column 'address': Modicon address {raw['address']} is a {table} "
                              f"address but table is {p['table']!r}")
        if len(errors) > n:
            continue
        o = {**p.pop("extra", {}), **{k: v for k, v in p.items() if k != "device"}}
        try:
            PointConfig.model_validate(o)
        except ValidationError as e:
            errors.extend(_pydantic_errors(e, prefix, POINT_COLUMNS))
            continue
        point_rows[key] = row_no
        dev["points"].append(o)

    if errors:
        return None, errors

    def locate(msg: str) -> str:
        """Best-effort sheet / row for a cross-row validation message."""
        for (dname, pname), r in point_rows.items():
            if f"{dname}/{pname}:" in msg:
                return f"Points row {r}"
        for dname, r in dev_rows.items():
            if f"'{dname}'" in msg or msg.startswith(f"{dname}:") or msg.startswith(f"{dname}/"):
                return f"Devices row {r}"
        for lid, r in lsn_rows.items():
            if f"'{lid}'" in msg:
                return f"Listeners row {r}"
        return "workbook"

    for name, d in devices.items():
        try:
            expand_device(DeviceConfig.model_validate(d))
        except ValidationError as e:
            errors.extend(_pydantic_errors(e, f"Devices row {dev_rows[name]}", DEVICE_COLUMNS))
        except ValueError as e:
            errors.append(f"{locate(str(e))}: {e}")
    if errors:
        return None, errors

    top: Dict[str, Any] = {"listeners": listeners or [l.model_dump(mode="json", exclude_unset=True,
                                                                    exclude_none=True) | {"id": l.id}
                                                       for l in base.listeners],
                           "devices": list(devices.values())}
    if "simulation" in base.model_fields_set:
        top = {"simulation": base.simulation.model_dump(mode="json", exclude_unset=True), **top}
    try:
        return SimConfig.model_validate(top), warnings
    except ValidationError as e:
        return None, [f"{locate(str(err['msg']))}: {str(err['msg']).removeprefix('Value error, ')}"
                      for err in e.errors()]

# Changelog

All notable changes to SME ModbusSim are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

First release (0.1.0): a Modbus device simulator with a live web UI.

**Protocol**
- Own asyncio protocol core with MBAP and RTU framers; function codes 01 / 02 / 03 / 04 / 05 / 06 / 0F / 10 / 16 (mask write register) / 17 (read/write multiple registers) / 2B-0E (Read Device Identification, served from `identification`), with standard exception responses.
- Listeners: Modbus TCP, Modbus RTU over TCP and serial RTU (`type: rtu`, pyserial: COM port, tty or pyserial URL; default 19200 8E1) with t3.5 / t1.5 frame timing (`strict_timing`), broadcast and 248-255 address handling; a port that cannot be opened marks only that listener as `error`.
- One listener serves many unit IDs and one device can be attached to several listeners; `unit_id_mode` `strict` / `any`, `unknown_unit` `ignore` / `exception`.
- Data types `bool`, `bit`, `uint16`, `int16`, `uint32`, `int32`, `float32`, `uint64`, `int64`, `float64`, `string`, `enum`, `sunspec_sf`, with byte order / word order (ABCD, CDAB, BADC, DCBA), scale, offset, `scale_ref` and `nullable` points.

**Scan and record**
- Read-only `scan` / `record` of a real device (Modbus TCP, RTU over TCP, serial RTU; only FC 01/02/03/04 and 2B/0E are ever sent, rate-limited, per-request timeout): `scan` finds readable addresses in the given ranges, samples them and suggests datatypes with a confidence and reason (float32 ABCD / CDAB; uint32 / int32 from a changing low word, a small constant high word or consecutive zero-high-word pairs; int16; string, but not runs of close or consecutive integers; bool), then saves a user template or adds a device; `record` polls points into a replay CSV. CLI subcommands with a confirmation prompt (`-y` to skip), and `/api/scan/*` / `/api/record/*` background jobs (one at a time, cancellable, progress as WebSocket event `scan`). In the web UI, Scan and Record dialogs show the plan and ask for confirmation before anything is sent, and the scan preview lets you change each guessed point before saving.
- After a recording, "Attach replay to points" (`POST /api/scan/apply/replay`, `{device?, loop?, dry_run?}`) gives each recorded column's point a `replay` behavior on the CSV, listing the behaviors it replaces; a CSV with empty first-row cells is refused. Guess reasons carry a `reason_code` / `reason_params` so the web UI shows them in English, Japanese and Chinese (`reason` stays English for the CLI and API).

**Simulation**
- Behaviors `sine`, `random`, `random_walk`, `expr`, `follow`, `ramp`, `counter`, `pulse`, `replay` (CSV); min / max clamping, integer counter wrap-around, read-only point protection; cross-device coupling (the demo site couples PCS, BMS and meter; `configs/demo-coupled-site.yaml` adds a PV / load / battery / grid-meter site whose meter balances load − PV − battery).
- Fault injection: runtime rules (exception, delay, no response, disconnect, bad CRC, truncation, wrong ids, busy, freeze, refuse connections) via the Faults tab, `/api/faults` and YAML / JSON presets.

**Configuration and templates**
- YAML configuration (validated with pydantic) with automatic address allocation for template points, per-device undefined-address policy and device identification fields.
- Generic templates `battery_pcs`, `bms`, `power_meter`, `env_sensor`, `custom`.
- SunSpec templates `sunspec_pv_inverter` / `sunspec_meter` / `sunspec_der` (models 701–705) / `sunspec_battery` (models 802 / 803), generated from the public SunSpec model definitions (Apache-2.0; vendored from sunspec/models @ 90b4a33). Not affiliated with, endorsed by, or certified by SunSpec Alliance.
- User-local templates in `~/.sme-modbus-sim/templates/` (`--templates-dir`, `SME_MODBUS_SIM_TEMPLATES`), reload from the UI, and save a device as a template.
- Excel point table: export / blank template / import (sheets `Listeners`, `Devices`, `Points`; `extra` YAML column; Modicon `40001` addresses; errors reported by sheet, row and column).

**Web UI, API and CLI**
- Web UI (English / Japanese / Simplified Chinese): point table with live values over WebSocket, write from the UI, per-point history and trend, Modbus traffic log (trace), statistics with push, fault injection, device management (add / edit / delete, enum editing) and a template browser; light and dark themes; usable on narrow windows.
- REST API and WebSocket: `/api/state`, `/api/templates`, `/api/history`, point write, simulation control, config get / put / save, `/api/trace`, `/api/stats`, `/api/faults`, `/api/version`, `/ws`. Error responses carry a readable `error` message next to FastAPI's `detail`; adding or duplicating a device returns `{ok, name, device}`.
- CLI: `sme-modbus-sim [run]` (web UI on port 8081 by default), `validate CONFIG` (exit 0 = valid, 2 = config error, message on stderr), `templates`, `scan`, `record`, `--version` (`sme-modbus-sim 0.1.0`).

**Packaging**
- Built on [sme-sim-common](https://pypi.org/project/sme-sim-common/) 1.x, the engine, web shell, behaviors, templates and Excel core shared with SME BACnetSim.
- Windows one-file exe, multi-arch Docker image (amd64 / arm64 / arm/v7) with offline image archives, CI on Linux / Windows / macOS (Python 3.11 - 3.13), documentation site.

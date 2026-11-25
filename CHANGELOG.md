# Changelog

All notable changes to SME ModbusSim are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- First release (0.1.0): a Modbus TCP and Modbus RTU-over-TCP device simulator with a live web UI.
- Own protocol core (asyncio): MBAP and RTU framers, function codes 01 / 02 / 03 / 04 / 05 / 06 / 0F / 10 (hex) with standard exception responses.
- Serial Modbus RTU listener (`type: rtu`, pyserial: COM port, tty or pyserial URL) with t3.5 / t1.5 frame timing (`strict_timing`), broadcast and 248-255 address handling; a port that cannot be opened marks only that listener as `error`.
- Function codes 16 (mask write register), 17 (read/write multiple registers) and 2B / 0E (Read Device Identification, basic / regular / extended / individual, served from `identification`).
- One listener serves many unit IDs, and one device can be attached to several listeners; `unit_id_mode` `strict` / `any`, `unknown_unit` `ignore` / `exception`.
- Data types `bool`, `bit`, `uint16`, `int16`, `uint32`, `int32`, `float32`, `uint64`, `int64`, `float64`, `string`, `enum` with byte order / word order (ABCD, CDAB, BADC, DCBA), scale and offset.
- YAML configuration (validated with pydantic) with automatic address allocation for template points, per-device undefined-address policy and device identification fields.
- Behaviors: `sine`, `random`, `random_walk`, `expr`, `follow`, `ramp`, `counter`, `pulse`, `replay` (CSV); min / max clamping, integer counter wrap-around, read-only point protection.
- Generic device templates: `battery_pcs`, `bms`, `power_meter`, `env_sensor`, `custom`; the demo site config couples PCS, BMS and meter.
- Web UI: point table with live values over WebSocket, write from the UI, per-point history, Modbus traffic log (trace), statistics; English / Japanese / Simplified Chinese.
- REST API and WebSocket: `/api/state`, `/api/templates`, `/api/history`, point write, simulation control, config get / put / save, `/api/trace`, `/api/stats`, `/api/version`, `/ws`.
- CLI: `sme-modbus-sim [run]`, `validate CONFIG`, `templates`.
- Excel point table: export / blank template / import (sheets `Listeners`, `Devices`, `Points`; `extra` YAML column; Modicon `40001` addresses; errors reported by sheet, row and column).
- User-local templates in `~/.sme-modbus-sim/templates/` (`--templates-dir`, `SME_MODBUS_SIM_TEMPLATES`), reload from the UI, and save a device as a template.
- Packaging: Windows one-file exe, multi-arch Docker image (amd64 / arm64 / arm/v7) with offline image archives, CI on Linux / Windows / macOS (Python 3.11 - 3.13), documentation site.

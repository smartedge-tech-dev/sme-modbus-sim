# SME ModbusSim

**English** | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

A Modbus TCP / RTU device simulator with a live web UI. Simulate battery PCS, BMS, power meters, environment
sensors — or any register map you describe in YAML — and watch the values change in the browser while a real
Modbus master (your EMS, a gateway, modpoll, pymodbus…) reads and writes them.

- **Many devices, one port.** One listener serves many unit IDs; a device can also be attached to several listeners.
  Modbus TCP and Modbus RTU over TCP (for serial gateways) are supported side by side.
- **Own protocol core.** Function codes 01, 02, 03, 04, 05, 06, 0F and 10 (hex) with standard exception responses.
  Modbus TCP / RTU compatible framing (MBAP and CRC-16).
- **All the data types.** `bool`, `int16` / `uint16`, `int32` / `uint32`, `int64` / `uint64`, `float32` / `float64`,
  `string`, `enum`, bit-in-register, with byte order and word order (ABCD / CDAB / BADC / DCBA), scale and offset.
- **Realistic behavior.** A PCS ramps toward its power set point, the BMS integrates SOC from the PCS power, the
  meter shows site load minus PCS power. Sine, random walk, expressions, counters, pulses and CSV replay.
- **Live UI.** Point table with live values over WebSocket, write from the browser, history per point, a Modbus
  traffic log. English, Japanese and Simplified Chinese.
- **Configurable.** Edit YAML, add devices from generic templates, save back to the config file.

Built on an asyncio protocol core written for this project, FastAPI and pyserial. No front-end build step.

SME ModbusSim is free and MIT-licensed, developed by [smart-edge (智届科技)](https://smartedge-tech.com) —
custom development is available, see [Commercial support & custom development](#commercial-support--custom-development).

## Quick start

### Windows exe

Download `sme-modbus-sim-<version>-windows.exe` from the [Releases](https://github.com/smartedge-tech-dev/sme-modbus-sim/releases)
page and run it. The browser opens the UI at http://127.0.0.1:8080/ .

### Docker

```bash
docker run --rm -p 8080:8080 -p 5020:5020 -p 5021:5021 ghcr.io/smartedge-tech-dev/sme-modbus-sim
```

Images are published for `linux/amd64`, `linux/arm64` and `linux/arm/v7`. Offline archives
(`docker load -i`) are attached to each release. Set `-e TZ=Asia/Tokyo` for a local clock (default UTC).
To use your own config: `-v ./my.yaml:/app/configs/my.yaml ... -c configs/my.yaml`.

### From source / pip

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python -m sme_modbus_sim run -c configs/demo-site.yaml
```

Open http://127.0.0.1:8080/ . Python 3.11 or newer.

| Command / option | Meaning |
|---|---|
| `sme-modbus-sim [run]` | Start the simulator (`run` is the default) |
| `-c, --config FILE` | YAML config (defaults to `configs/demo-site.yaml`) |
| `--host 0.0.0.0` | Web UI bind address (default localhost only) |
| `--http-port 8080` | Web UI port |
| `--no-browser` | Do not open the browser |
| `validate CONFIG` | Check a config file and exit |
| `templates` | List the device templates |

The demo config has four devices: a battery PCS (unit 1), a BMS (unit 2), a grid meter (unit 3) on Modbus TCP
port **5020**, and a room sensor (unit 4) reachable only through the RTU-over-TCP listener on port **5021**.

## Testing with a Modbus master

```bash
# modpoll (addresses are 1-based by default; -0 for protocol addresses)
modpoll -m tcp -p 5020 -a 1 -0 -r 0 -c 4 -t 4 127.0.0.1
modpoll -m enc -p 5021 -a 4 -0 -r 0 -c 2 -t 3 127.0.0.1      # RTU over TCP
```

```python
from pymodbus.client import ModbusTcpClient

client = ModbusTcpClient("127.0.0.1", port=5020)
client.connect()
print(client.read_holding_registers(0, count=2, device_id=1).registers)
client.write_register(0, 200, device_id=1)   # PCS-1 p_setpoint = 20.0 kW (int16 x0.1)
client.close()
```

## Roadmap

- **0.1.0** — Modbus TCP and RTU over TCP, FC 01–06 / 0F / 10, all data types, YAML, web UI, generic templates,
  Windows exe, Docker, CI.
- **0.2.0** — serial RTU (COM port / `/dev/ttyUSB*`), FC 16 mask write register, FC 17 read/write multiple
  registers, FC 2B/0E read device identification, fault injection UI (delay, drop, exceptions, wrong CRC),
  Excel point-list import / export, user-local templates, and templates generated from the public SunSpec
  model definitions.

> Not affiliated with, endorsed by, or certified by SunSpec Alliance. SunSpec templates: `sunspec_pv_inverter` (models 1, 103, 120-123, 160) and `sunspec_meter` (models 1, 203), generated from the public SunSpec model definitions (Apache-2.0).

## Commercial support & custom development

SME ModbusSim is developed by **smart-edge** — Nanjing Smart-Edge Technology Co., Ltd. (南京智届科技有限公司, 智届科技).
Since its founding in 2023 the company has developed end-to-end software for distributed energy resources
such as solar, batteries and EVs, from equipment control to the cloud and electricity markets. Its products
include the AERIS RA platform (リソースアグリゲーター向け) and an AC system (アグリゲーションコーディネーター) for
the Japanese electricity markets. The team supports projects in Japanese, from requirements definition
through design, implementation, operation and maintenance.

Services:

- Energy management systems (EMS), cloud and on-site
- RA / AC systems for electricity markets (JEPX, EPRX, OCCTO)
- Edge IoT and equipment-control software
- Equipment integration: Modbus, MQTT, BACnet, ECHONET Lite, OpenADR 2.0b
- Energy forecasting and data analytics
- Cloud infrastructure design, build and operation

Need a feature added to SME ModbusSim, or help integrating your devices or EMS? Get in touch:

- Website: <https://smartedge-tech.com>
- Contact page: <https://smartedge-tech.com/contact/>
- E-mail: [business@smartedge-tech.com](mailto:business@smartedge-tech.com)

## License

[MIT](LICENSE)

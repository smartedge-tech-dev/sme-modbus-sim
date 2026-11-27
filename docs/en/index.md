---
title: Free Modbus TCP / RTU slave simulator with a web UI
description: Free, open-source Modbus slave (server) simulator with a live web UI for Windows, Linux and Docker. Modbus TCP, RTU, ASCII, UDP and TLS; test an EMS, SCADA or gateway without real devices.
---

# SME ModbusSim

A free, open-source Modbus TCP / RTU slave (server) simulator with a live web UI, for Windows, Linux
and Docker. Simulate battery PCS, BMS, power meters, environment
sensors — or any register map you describe in YAML — and watch the values change in the browser while a real
Modbus master (your EMS, a gateway, modpoll, pymodbus…) reads and writes them.

![Live register values, trend and Modbus traffic](../assets/screenshots/en/live.gif)

- **Many devices, one port.** One listener serves many unit IDs; a device can be attached to several listeners.
  Modbus TCP, Modbus UDP, Modbus/TCP Security (TLS), Modbus RTU over TCP and serial RTU are supported side by side.
- **Own protocol core.** Function codes 01, 02, 03, 04, 05, 06, 0F, 10, 16, 17 and 2B/0E (hex) with standard exception responses; serial listeners also answer the diagnostic function codes 07, 08, 0B, 0C and 11.
- **All the data types**, with byte order, word order, scale and offset.
- **Realistic behavior.** Sine, random walk, expressions, ramp / follow, counters, pulses and CSV replay;
  generic templates for battery PCS, BMS, power meter and environment sensor.
- **Live UI** in English, Japanese and Simplified Chinese, plus a REST / WebSocket API.

SME ModbusSim is free and MIT-licensed, developed by [smart-edge (智届科技)](https://smartedge-tech.com) —
custom development is available, see [Commercial support & custom development](commercial-support.md).

Also included: [fault injection](faults.md), [Excel point lists](excel.md), user-local templates and SunSpec PV inverter /
meter / DER / battery templates ([templates](behaviors-templates.md)), and [device management](device-management.md) in the UI, and read-only scan / record of real devices ([quick start](quick-start.md)).
Not affiliated with, endorsed by, or certified by SunSpec Alliance; the SunSpec templates are generated from the public
SunSpec model definitions (Apache-2.0).

Released in 0.2.0: serial Modbus ASCII; built-in pty pair for serial testing without drivers (Linux / macOS / WSL2); serial diagnostic function codes 07 / 08 / 0B / 0C / 11.

Released in 0.3.0: Modbus UDP listener; Modbus/TCP Security (TLS) listener with client certificates and role-based authorization; UDP and TLS for the read-only scan / record client.

## Typical uses

- **Test a Modbus master without hardware.** Point your EMS, SCADA, PLC or gateway at the simulator instead
  of real slaves.
- **Reproduce a site on your desk.** Import an [Excel point list](excel.md), start from the
  [battery PCS, BMS, meter and SunSpec templates](behaviors-templates.md), or scan and record a real device
  ([quick start](quick-start.md)).
- **Test error handling.** Timeouts, exception responses and other faults with
  [fault injection](faults.md).

## More simulators from smart-edge

SME ModbusSim is one of a series of free, MIT-licensed protocol simulators by smart-edge. They share the
same web UI and conventions, so what you learn in one carries over to the others.

| Simulator | What it simulates | Docs |
|---|---|---|
| [SME BACnetSim](https://github.com/smartedge-tech-dev/sme-bacnet-sim) | BACnet/IP device simulator | [Docs](https://smartedge-tech-dev.github.io/sme-bacnet-sim/) |
| **SME ModbusSim** (this project) | Modbus TCP / RTU / UDP / TLS slave (server) simulator | [Docs](https://smartedge-tech-dev.github.io/sme-modbus-sim/) |
| [SME OCPPSim](https://github.com/smartedge-tech-dev/sme-ocpp-sim) | OCPP 1.6J / 2.0.1 charging station (charge point) simulator | [Docs](https://smartedge-tech-dev.github.io/sme-ocpp-sim/) |

All open-source tools from smart-edge: <https://smartedge-tech.com/open-source/>

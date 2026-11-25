# Configuration

A configuration file is YAML with three sections: `simulation`, `listeners` and `devices`. The demo is
`configs/demo-site.yaml`; `sme-modbus-sim validate FILE` checks a file without starting anything.

```yaml
simulation: {tick_seconds: 1, time_scale: 1, history_size: 600, running: true}

listeners:
  - {id: tcp1, type: tcp, host: 0.0.0.0, port: 5020, unit_id_mode: strict, unknown_unit: ignore}
  - {id: gw1, type: rtu_over_tcp, host: 0.0.0.0, port: 5021}

devices:
  - name: Meter-1
    unit_id: 3
    template: power_meter
    word_order: little
    points:
      - {name: serial_number, value: "MTR00001"}
```

## simulation

| Key | Meaning |
|---|---|
| `tick_seconds` | Behavior update interval in seconds (above 0.05, up to 60) |
| `time_scale` | Simulated seconds per real second (above 0, up to 3600) |
| `history_size` | Samples kept per point for the UI chart |
| `running` | Start running or paused |

## listeners

| Key | Meaning |
|---|---|
| `id` | Name used by `devices[].listeners` |
| `type` | `tcp` (Modbus TCP, MBAP), `rtu_over_tcp` (RTU frames with CRC-16 over a TCP socket) or `rtu` (serial line) |
| `host`, `port` | `tcp` / `rtu_over_tcp`: bind address and TCP port (default 5020; ports below 1024 need privileges). `rtu`: `port` is the serial device (`COM5`, `/dev/ttyUSB0`) or a pyserial URL (`socket://host:port`, `rfc2217://...`); `host` is ignored |
| `unit_id_mode` | `strict`: only configured unit IDs answer. `any`: every unit ID is routed to `default_unit` |
| `unknown_unit` | In `strict` mode: `ignore` (no response, like a real bus) or `exception` (gateway exception) |
| `default_unit` | Unit ID used by `any` mode and for TCP unit IDs 0 / 255 |
| `baudrate`, `bytesize`, `parity`, `stopbits` | `rtu` only. Defaults 19200 (spec default), 8, `E`, 1. `parity`: `E` / `O` / `N`; with `N` the default is 2 stop bits (Serial Line V1.02). `N` + `stopbits: 1` is accepted as a common but non-conforming setting |
| `strict_timing` | `rtu` only (default `false`). `true`: a gap longer than t1.5 inside a frame drops it and exactly t3.5 ends a frame. `false`: no t1.5 check and frames end after max(t3.5, 20 ms) of silence, because USB-serial adapters deliver bytes in bursts |

### Serial RTU (`type: rtu`)

```yaml
listeners:
  - {id: com1, type: rtu, port: COM5, baudrate: 19200, parity: E}
```

- Frame end = silence of t3.5 (3.5 character times of 11 bits; fixed 1.75 ms above 19200 baud); requests of known length are answered without waiting for the silence.
- Unit IDs 1-247 answer as configured; 0 is broadcast (write function codes run on every device of the listener, never answered); 248-255 are ignored.
- `unknown_unit: exception` has no effect on a serial line (unknown units never answer).
- If the port cannot be opened, the listener reports `status: error` in `/api/state` and the other listeners keep running; a configuration reload retries.

### Function codes

01, 02, 03, 04, 05, 06, 0F, 10, 16 (mask write register), 17 (read/write multiple registers, write before read) and 2B / 0E (Read Device Identification: read codes 01 basic, 02 regular, 03 extended stream, 04 individual; long answers use More Follows). Writes through 16 / 17 pass the same checks as 06 / 10 (read-only points, partial multi-register values, min / max). Other function codes answer exception 01.

## devices

| Key | Meaning |
|---|---|
| `name`, `description` | Device name (unique) and free text |
| `unit_id` | 1-247 |
| `listeners` | Listener IDs this device is attached to (default: all) |
| `template`, `params` | Generate points from a template, see [Behaviors & templates](behaviors-templates.md) |
| `byte_order`, `word_order` | Device defaults: `big` or `little` |
| `undefined_address` | `exception` (illegal data address, default) or `zero` for reads outside defined points |
| `identification` | `vendor_name`, `product_code`, `revision`, `vendor_url`, `product_name`, `model_name`, `user_application_name` (served by FC 2B / 0E; defaults `SmartEdge`, `SME-MODBUS-SIM`, program version) |
| `points` | Points of this device; entries with the name of a template point override it |
| `removed` | Template points deleted by the user |

## points

| Key | Meaning |
|---|---|
| `name`, `description`, `units` | Identification |
| `table` | `coil`, `discrete`, `input` or `holding` |
| `address` | 0-based protocol address; omitted addresses are allocated automatically |
| `datatype` | `bool`, `bit`, `uint16`, `int16`, `uint32`, `int32`, `float32`, `uint64`, `int64`, `float64`, `string`, `enum` |
| `count` | Registers of a `string` (two characters each) |
| `bit` | Bit number 0-15 for `datatype: bit` (one bit of a holding / input register) |
| `byte_order`, `word_order` | Override the device defaults |
| `scale`, `offset` | Register value = (engineering value - offset) / scale |
| `access` | `r` or `rw`; writes to `r` points return exception 02 |
| `value` | Initial value |
| `min`, `max` | Clamp the value |
| `labels` | `enum` mapping, raw value to label (quote labels such as `"ON"`) |
| `behavior` | See [Behaviors & templates](behaviors-templates.md) |

### Byte order and word order

For multi-register values, `word_order` decides which register comes first and `byte_order` the byte order inside
a register. A float32 of 1.0 (0x3F800000) is sent as:

| byte / word order | Registers | Name |
|---|---|---|
| big / big | `3F80 0000` | ABCD (Modbus standard) |
| big / little | `0000 3F80` | CDAB (common for meters) |
| little / big | `803F 0000` | BADC |
| little / little | `0000 803F` | DCBA |

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
| `tick_seconds` | Behavior update interval (0.05 - 60 s) |
| `time_scale` | Simulated seconds per real second |
| `history_size` | Samples kept per point for the UI chart |
| `running` | Start running or paused |

## listeners

| Key | Meaning |
|---|---|
| `id` | Name used by `devices[].listeners` |
| `type` | `tcp` (Modbus TCP, MBAP) or `rtu_over_tcp` (RTU frames with CRC-16 over a TCP socket) |
| `host`, `port` | Bind address and port (default 5020; ports below 1024 need privileges) |
| `unit_id_mode` | `strict`: only configured unit IDs answer. `any`: every unit ID is routed to `default_unit` |
| `unknown_unit` | In `strict` mode: `ignore` (no response, like a real bus) or `exception` (gateway exception) |
| `default_unit` | Unit ID used by `any` mode and for TCP unit IDs 0 / 255 |

## devices

| Key | Meaning |
|---|---|
| `name`, `description` | Device name (unique) and free text |
| `unit_id` | 1-247 |
| `listeners` | Listener IDs this device is attached to (default: all) |
| `template`, `params` | Generate points from a template, see [Behaviors & templates](behaviors-templates.md) |
| `byte_order`, `word_order` | Device defaults: `big` or `little` |
| `undefined_address` | `exception` (illegal data address, default) or `zero` for reads outside defined points |
| `identification` | `vendor_name`, `product_code`, `revision`, `vendor_url`, `product_name`, `model_name` |
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

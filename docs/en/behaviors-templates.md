# Behaviors & templates

## Behaviors

A point with a `behavior` is updated every tick; others keep the value that was last written.

| `type` | Parameters | Result |
|---|---|---|
| `sine` | `min`, `max`, `period`, `phase`, `noise` | Sine wave between min and max |
| `random` | `min`, `max` | Uniform noise |
| `random_walk` | `min`, `max`, `step` | Bounded random walk |
| `expr` | `expr` | Expression over other points of the same device, e.g. `temperature > 26` |
| `follow` | `target` (point name or expression), `tau`, `noise` | First-order lag toward another point |
| `ramp` | `target`, `rate` (number or expression) | Rate-limited tracking of another point (units per second) |
| `counter` | `rate` (number or expression, per second) | Integrates the rate, e.g. energy from power; integer types wrap around at their limit |
| `pulse` | `rate` (pulses per second) | Integer pulse counter |
| `replay` | `file`, `column`, `interpolate`, `loop`, `offset` | Plays a CSV column on the simulated clock |

Values written by a Modbus master or from the UI are validated against the data type, `min` / `max` and `access`.

## Templates

A template generates a device's points, with addresses allocated automatically, and couples devices through
`params` (for example a PCS references its BMS). Add one with `template: <name>` and your own `params`; points of
the same name in the device override the template's.

| Template | Content |
|---|---|
| `battery_pcs` | Run command, P / Q set points with ramp, actual P / Q, DC voltage, SOC limits, status |
| `bms` | SOC integrated from the PCS power, SOH, cell voltage / temperature, alarms |
| `power_meter` | Three-phase voltage / current / power, frequency, energy counters |
| `env_sensor` | Temperature and humidity |
| `custom` | Empty device, define all points yourself |

`sme-modbus-sim templates` lists the parameters. The templates are generic and original; no vendor register maps
are built in. Templates generated from the public SunSpec model definitions are planned for 0.2.0 (not affiliated
with, endorsed by, or certified by SunSpec Alliance).


## User-local templates

Your own templates live outside the installation, in a directory of YAML files:
`--templates-dir DIR` on the command line, else `$SME_MODBUS_SIM_TEMPLATES`, else `~/.sme-modbus-sim/templates/`.
They are loaded at startup (before the configuration, so devices can use them) and again with
**Templates › Reload user templates** (`POST /api/templates/reload`). A user template with the name of a built-in
template replaces it. A file with errors is skipped and reported; it never stops the simulator.

```yaml
# ~/.sme-modbus-sim/templates/my_inverter.yaml
name: my_inverter          # default: the file name
title: My PV inverter
description: Register map of our inverter
params: {rated_kw: 10}     # defaults; a device overrides them with params:
points:                    # same schema as a device's points:
  - {name: p_limit, table: holding, address: 0, datatype: uint16, scale: 0.1, max: "{rated_kw}"}
  - {name: p_actual, table: input, address: 0, datatype: int16, scale: 0.1,
     behavior: {type: expr, expr: "min(p_limit, {rated_kw} * 0.8)"}}
```

Parameters are substituted only inside **string** values with Python `str.format` rules: `{rated_kw}` is replaced
by the parameter value. A string that is exactly one placeholder (`"{rated_kw}"`) takes the value with its type, so
it works in numeric fields. Write literal braces as `{{` and `}}`.

**Save as template** in the device header (`POST /api/templates/from-device/{device}` with
`{"name": ..., "overwrite": false}`) writes the device's expanded points as a template file into this directory,
for example after importing a vendor register map from [Excel](excel.md). `GET /api/templates` marks each template
with `source` (`builtin` / `user`) and, for user templates, the file `path`.

## SunSpec templates

`sunspec_pv_inverter` and `sunspec_meter` are templates generated from the public SunSpec model definitions
(Apache-2.0, vendored unmodified from [sunspec/models](https://github.com/sunspec/models) at commit
`90b4a331dcca1d6eac69c1bead952fddcc5852e0`); they implement SunSpec Modbus models.
Not affiliated with, endorsed by, or certified by SunSpec Alliance.

| Template | Models | Params |
|---|---|---|
| `sunspec_pv_inverter` | 1, 103, 120, 121, 122, 123, 160 | `rated_w`, `mppt_count`, `ac_voltage_v`, `frequency_hz`, `dc_voltage_v`, `serial_number`, `base_address` |
| `sunspec_meter` | 1, 203 | `base_load_w`, `voltage_v`, `frequency_hz`, `pv_device`, `serial_number`, `base_address` |

- Holding registers from `base_address` (40000, 0 or 50000): `SunS` marker, each model (`ID`, `L`, points),
  end model (`0xFFFF`, `L = 0`). Point names are `m<id>_<Name>` (e.g. `m103_W`); repeating groups are
  `m160_module1_DCW`, ...
- Scale factors: datatype `sunspec_sf` (int16) and point field `scale_ref: <sf point>`; the engineering value is
  `raw x 10^sf`, with the sf read from the referenced point at encode / decode time.
- Not implemented values (`0x8000` int16 / sunssf, `0xFFFF` uint16, `0x80000000` int32, `0xFFFFFFFF` uint32,
  NaN float32) appear as `null` in the UI and API (point field `nullable: true`). Optional model points without a
  value are left not implemented; accumulators are never null.
- Inverter: `m103_W` follows a daylight curve, capped by `m123_WMaxLimPct` when `m123_WMaxLim_Ena = 1`, and 0 when
  `m123_Conn = 0`; `m103_WH` accumulates; `m103_St` = OFF / SLEEPING / MPPT. Meter: `m203_W` = load profile minus
  `pv_device`'s `m103_W` (> 0 import), `m203_TotWhImp` / `m203_TotWhExp` accumulate.

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

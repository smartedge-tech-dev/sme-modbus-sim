# 行为与模板

## 行为（behavior）

带 `behavior` 的点每个 tick 更新一次；其他点保持最后写入的值。

| `type` | 参数 | 结果 |
|---|---|---|
| `sine` | `min`, `max`, `period`, `phase`, `noise` | min 到 max 之间的正弦波 |
| `random` | `min`, `max` | 均匀随机噪声 |
| `random_walk` | `min`, `max`, `step` | 有界随机游走 |
| `expr` | `expr` | 引用同一设备其他点的表达式，例如 `temperature > 26` |
| `follow` | `target` (point name or expression), `tau`, `noise` | 对另一个点的一阶滞后跟随 |
| `ramp` | `target`, `rate` (number or expression) | 对另一个点的限速跟随（单位/秒） |
| `counter` | `rate`（数值或表达式，每秒） | 对速率积分（例如由功率得到电量）；整型到上限后回绕 |
| `pulse` | `rate`（每秒脉冲数） | 整数脉冲计数器 |
| `replay` | `file`, `column`, `interpolate`, `loop`, `offset` | 按模拟时钟回放 CSV 的一列 |

由 Modbus 主站或 UI 写入的值，会按数据类型、`min` / `max` 和 `access` 进行校验。

## 模板

模板自动分配地址并生成设备的点表，还可通过 `params` 把设备关联起来（例如 PCS 引用对应的 BMS）。
使用 `template: <名称>` 加上自己的 `params`；设备里同名的点会覆盖模板中的定义。

| 模板 | 内容 |
|---|---|
| `battery_pcs` | 运行指令、P / Q 设定值（带斜率）、实际 P / Q、直流电压、SOC 限制、状态 |
| `bms` | 由 PCS 功率积分得到的 SOC、SOH、单体电压 / 温度、告警 |
| `power_meter` | 三相电压 / 电流 / 功率、频率、电能计数器 |
| `env_sensor` | 温度与湿度 |
| `custom` | 空设备，所有点自行定义 |

参数可用 `sme-modbus-sim templates` 查看。模板为通用的原创定义，不内置任何厂商的寄存器表。由公开的 SunSpec
模型定义生成的模板计划在 0.2.0 提供（与 SunSpec Alliance 无任何关联，未获其认可或认证）。

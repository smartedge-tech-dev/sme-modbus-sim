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

参数可用 `sme-modbus-sim templates` 查看。模板为通用的原创定义，不内置任何厂商的寄存器表。`sunspec_pv_inverter` 与 `sunspec_meter`
由公开的 SunSpec 模型定义（Apache-2.0）生成，实现 SunSpec Modbus 模型（与 SunSpec Alliance 无任何关联，未获其认可或认证）。
自有厂商表可通过 [Excel](excel.md) 或用户本地模板私下导入。


## User-local templates

自己的模板保存在安装目录之外、存放 YAML 文件的目录中：命令行 `--templates-dir DIR`，否则 `$SME_MODBUS_SIM_TEMPLATES`，
否则 `~/.sme-modbus-sim/templates/`。启动时（先于配置加载，因此设备可以使用）以及通过 **模板 › 重新加载用户模板**
（`POST /api/templates/reload`）加载。与内置模板同名的用户模板会替换内置模板。有错误的文件会被跳过并报告，不会使模拟器停止。

```yaml
# ~/.sme-modbus-sim/templates/my_inverter.yaml
name: my_inverter          # 默认：文件名
title: My PV inverter
description: Register map of our inverter
params: {rated_kw: 10}     # 默认值；设备用 params: 覆盖
points:                    # 与设备的 points: 相同的 schema
  - {name: p_limit, table: holding, address: 0, datatype: uint16, scale: 0.1, max: "{rated_kw}"}
  - {name: p_actual, table: input, address: 0, datatype: int16, scale: 0.1,
     behavior: {type: expr, expr: "min(p_limit, {rated_kw} * 0.8)"}}
```

参数只在**字符串**值中按 Python `str.format` 规则替换（`{rated_kw}` → 参数值）。恰好只有一个占位符的字符串
（`"{rated_kw}"`）会连同类型取得参数值，因此也可用于数值字段。字面量花括号写作 `{{` / `}}`。

设备标题栏中的 **另存为模板**（`POST /api/templates/from-device/{device}`，请求体 `{"name": ..., "overwrite": false}`）
会把设备展开后的点作为模板文件写入该目录，例如在用 [Excel](excel.md) 导入厂商寄存器表之后。`GET /api/templates`
为每个模板标注 `source`（`builtin` / `user`），用户模板还会给出文件 `path`。

## SunSpec 模板

`sunspec_pv_inverter` 与 `sunspec_meter` 是由公开的 SunSpec 模型定义（Apache-2.0，从
[sunspec/models](https://github.com/sunspec/models) 提交 `90b4a331dcca1d6eac69c1bead952fddcc5852e0`
原样收录）生成的模板，实现了 SunSpec Modbus 模型。与 SunSpec Alliance 无任何关联，未获其认可或认证。

| 模板 | 模型 | 参数 |
|---|---|---|
| `sunspec_pv_inverter` | 1, 103, 120, 121, 122, 123, 160 | `rated_w`, `mppt_count`, `ac_voltage_v`, `frequency_hz`, `dc_voltage_v`, `serial_number`, `base_address` |
| `sunspec_meter` | 1, 203 | `base_load_w`, `voltage_v`, `frequency_hz`, `pv_device`, `serial_number`, `base_address` |

- 从 `base_address`（40000 / 0 / 50000）起在保持寄存器中依次放置 `SunS` 标识、各模型（`ID`、`L`、点）、
  结束模型（`0xFFFF`、`L = 0`）。点名为 `m<id>_<Name>`（如 `m103_W`），重复组为 `m160_module1_DCW` 等。
- 比例因子：数据类型 `sunspec_sf`（int16）与点字段 `scale_ref: <sf 点>`；工程值 = `raw x 10^sf`，
  sf 在编码 / 解码时从被引用的点读取。
- 未实现值（int16 / sunssf `0x8000`、uint16 `0xFFFF`、int32 `0x80000000`、uint32 `0xFFFFFFFF`、float32 NaN）
  在 UI 与 API 中显示为 `null`（点字段 `nullable: true`）。无值的可选点视为未实现；累计量不会为 null。
- 逆变器：`m103_W` 随日照曲线变化，`m123_WMaxLim_Ena = 1` 时受 `m123_WMaxLimPct` 限制，`m123_Conn = 0` 时为 0；
  `m103_WH` 累计。电表：`m203_W` = 负荷曲线 − `pv_device` 的 `m103_W`（> 0 买电），`m203_TotWhImp` / `m203_TotWhExp` 累计。

# 配置

配置文件为 YAML，包含 `simulation`、`listeners` 和 `devices` 三个部分。演示配置是 `configs/demo-site.yaml`；
`sme-modbus-sim validate FILE` 可在不启动的情况下校验文件。

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

| 键 | 含义 |
|---|---|
| `tick_seconds` | 行为更新间隔（秒，大于 0.05 且不超过 60） |
| `time_scale` | 每个真实秒对应的模拟秒数 （大于 0 且不超过 3600）|
| `history_size` | 每个点为 UI 曲线保留的采样数 |
| `running` | 启动时是运行还是暂停 |

## listeners

| 键 | 含义 |
|---|---|
| `id` | 供 `devices[].listeners` 引用的名称 |
| `type` | `tcp`（Modbus TCP，MBAP）、`rtu_over_tcp`（TCP 套接字上带 CRC-16 的 RTU 帧）`rtu`（串口，RTU）或 `ascii`（串口，Modbus ASCII） |
| `host`, `port` | `tcp` / `rtu_over_tcp`：绑定地址与 TCP 端口（默认 5020；1024 以下需要权限）。`rtu` / `ascii`：`port` 为串口设备（`COM5`、`/dev/ttyUSB0`）、pyserial URL（`socket://host:port`、`rfc2217://...`）或 `pty`（见下文），`host` 被忽略 |
| `unit_id_mode` | `strict`：只响应已配置的站号。`any`：所有站号都路由到 `default_unit` |
| `unknown_unit` | `strict` 模式下：`ignore`（与真实总线一样不响应）或 `exception`（网关异常） |
| `default_unit` | `any` 模式以及 TCP 站号 0 / 255 使用的站号 |
| `baudrate`, `bytesize`, `parity`, `stopbits` | 仅 `rtu` / `ascii`。默认 19200（规格默认值）、8（`ascii` 为 7）、`E`、1。`rtu` 只能 8 位数据位，`ascii` 可为 7 或 8。`parity`：`E` / `O` / `N`；`N` 时默认 2 位停止位（Serial Line V1.02）。`N` + `stopbits: 1` 作为常见但不符合规格的设置也可接受 |
| `strict_timing` | 仅 `rtu`（默认 `false`）。`true`：帧内字符间隔超过 t1.5 即丢弃该帧，静默恰好 t3.5 即帧结束。`false`：不检查 t1.5，静默 max(t3.5, 20 ms) 视为帧结束（USB 转串口适配器会成批送出字节） |
| `char_timeout` | 仅 `ascii`（默认 1 s）。超过这么久没有新字符，就丢弃未完成的帧 |
| `link` | 仅 `port: pty`。指向 pty 对中被测设备那一端的符号链接路径（例如 `/tmp/ttyV0`） |

### 串口 RTU（`type: rtu`）

```yaml
listeners:
  - {id: com1, type: rtu, port: COM5, baudrate: 19200, parity: E}
```

- 帧结束 = t3.5 静默（3.5 个 11 位字符时间；19200 baud 以上固定 1.75 ms）；长度可推定的请求无需等待静默即应答。
- 站号 1-247 按配置应答；0 为广播（写类功能码在该监听器的所有设备上执行，不应答）；248-255 忽略。
- 串口上 `unknown_unit: exception` 无效（未知站号一律不应答）。
- 串口无法打开时，该监听器在 `/api/state` 中为 `status: error`，其他监听器照常运行；重新加载配置时会重试。

### 串口 ASCII（`type: ascii`）

```yaml
listeners:
  - {id: com2, type: ascii, port: COM6}   # 19200 baud, 7E1
```

- 帧 = `:`，地址、PDU 和 LRC 的十六进制字符，再加 CR LF（Serial Line V1.02 第 2.5.2 节）。接受小写十六进制，应答用大写。
- `:` 之前的字符忽略；收到新的 `:`，或字符间隔超过 `char_timeout`，都会丢弃未完成的帧。LRC 不符、十六进制字符数为奇数、超过 513 个字符的帧不应答。
- `port: pty` 同样适用于 ASCII。寻址、广播、故障注入与 RTU 相同；`corrupt_crc` 改为把 LRC 取反。

### 伪终端对（`port: pty`）

在 Linux、macOS 和 WSL2 上，ModbusSim 可以自己创建虚拟串口对，不需要驱动，也不需要 root：

```yaml
listeners:
  - {id: vcom, type: rtu, port: pty, link: /tmp/ttyV0}
```

- 串口对的一端由 ModbusSim 持有；被测设备把 `link`（或日志、UI、`/api/state` 中 `device` 显示的 `/dev/pts/N`）当作串口打开。
- `link` 在启动时创建，退出时删除。路径已存在时，该监听器为 `status: error`，不动已有的文件。请放在有写权限的目录（`/dev` 需要 root）。
- 被测设备可以随时关闭并重新打开串口。
- 局限：pty 不传 RTS / CTS / DTR，波特率和校验位设置无效，也不按波特率送出字节，因此测不了 t1.5 / t3.5，请保持 `strict_timing: false`。
- 被测设备必须以 8N1 打开 pty：在 Linux 上（WSL2 5.15 内核实测），给 pty 设偶/奇校验或 7 位数据位会报 `EINVAL`。线路上的字节不受影响，所以 ASCII 或 8E1 的主站把串口参数改成 8N1 即可。
- Windows 上不可用：配置校验会报错，并提示可选方案：签名的虚拟串口驱动、两个 USB-RS485 适配器互接，或 `rtu_over_tcp`。

### 功能码

01、02、03、04、05、06、0F、10、16（Mask Write Register）、17（Read/Write Multiple registers，先写后读）以及 2B / 0E（Read Device Identification：读取码 01 basic、02 regular、03 extended 流式读取，04 单个对象；过长的应答用 More Follows 分段）。经 16 / 17 的写入与 06 / 10 适用相同检查（只读点、多寄存器值部分写入、min / max）。其他功能码返回异常 01。

## devices

| 键 | 含义 |
|---|---|
| `name`, `description` | 设备名（唯一）和说明 |
| `unit_id` | 1-247 |
| `listeners` | 设备挂接的监听器 ID（默认：全部） |
| `template`, `params` | 由模板生成点表，见[行为与模板](behaviors-templates.md) |
| `byte_order`, `word_order` | 设备默认值：`big` 或 `little` |
| `undefined_address` | 读取未定义地址时：`exception`（非法数据地址，默认）或 `zero` |
| `identification` | `vendor_name`、`product_code`、`revision`、`vendor_url`、`product_name`、`model_name`, `user_application_name` （由 FC 2B / 0E 应答；默认 `SmartEdge`、`SME-MODBUS-SIM`、程序版本） |
| `points` | 本设备的点；与模板点同名的条目会覆盖模板定义 |
| `removed` | 用户删除的模板点 |

## points

| 键 | 含义 |
|---|---|
| `name`, `description`, `units` | 名称、说明、单位 |
| `table` | `coil`、`discrete`、`input` 或 `holding` |
| `address` | 从 0 开始的协议地址；省略时自动分配 |
| `datatype` | `bool`、`bit`、`uint16`、`int16`、`uint32`、`int32`、`float32`、`uint64`、`int64`、`float64`、`string`、`enum` |
| `count` | `string` 占用的寄存器数（每个寄存器 2 个字符） |
| `bit` | `datatype: bit` 的位号 0-15（holding / input 寄存器中的一位） |
| `byte_order`, `word_order` | 覆盖设备默认值 |
| `scale`, `offset` | 寄存器值 = (工程值 − offset) / scale |
| `access` | `r` 或 `rw`；写入 `r` 点会返回异常 02 |
| `value` | 初始值 |
| `min`, `max` | 数值上下限 |
| `labels` | `enum` 映射（原始值到标签）；`"ON"` 之类的标签请加引号 |
| `behavior` | 见[行为与模板](behaviors-templates.md) |

### 字节序与字序

对于跨多个寄存器的值，`word_order` 决定先发送哪个寄存器，`byte_order` 决定寄存器内的字节顺序。
float32 的 1.0（0x3F800000）发送如下：

| byte / word order | 寄存器 | 名称 |
|---|---|---|
| big / big | `3F80 0000` | ABCD（Modbus 标准） |
| big / little | `0000 3F80` | CDAB（电能表常见） |
| little / big | `803F 0000` | BADC |
| little / little | `0000 803F` | DCBA |

## 耦合站点示例

`configs/demo-coupled-site.yaml` 只用通用模板和表达式，把 5 台设备耦合在同一个 TCP 监听器（端口 5020）上，
方便 EMS 对其做闭环测试：

| 设备（unit） | 点位 | 行为 |
|---|---|---|
| PV-1（1，`custom`） | `p_ac` | `40 * max(0, sin(pi * (hour - 6) / 12)) * cloud_factor`（06:00–18:00 发电；`cloud_factor` 为 0.7–1.0 的随机游走） |
| Load-1（2，`custom`） | `active_power` | `20 + 15 * exp(-((hour - 8) ** 2) / 4) + 25 * exp(-((hour - 19) ** 2) / 6) + noise(0.5)` |
| PCS-1（3，`battery_pcs`） | `p_actual` | `run_command` 为开时以 10 kW/s 斜坡逼近 `p_setpoint`（受 BMS SOC 限制） |
| BMS-1（4，`bms`） | `soc` | 速率为 `-p_actual / capacity_kwh * 100 / 3600`（%/秒）的 counter，钳位在 0–100 |
| Meter-1（5，`power_meter`） | `active_power` | `pv('Load-1', 'active_power') - pv('PV-1', 'p_ac') - pv('PCS-1', 'p_actual')` |

符号：PCS 功率 + 放电 / - 充电；电表功率 + 购电 / - 上网，购电 / 上网电能计数器对其积分。
行为按文件顺序逐台设备执行，因此放在最后的电表使用的是同一 tick 的值。
试一试：向 unit 3 写 holding 0 = 200（20.0 kW）和 coil 0 = 1，电表购电功率会下降 20 kW，BMS-1 的 `soc` 随之下降。

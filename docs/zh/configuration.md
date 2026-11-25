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
| `type` | `tcp`（Modbus TCP，MBAP）或 `rtu_over_tcp`（TCP 套接字上带 CRC-16 的 RTU 帧） |
| `host`, `port` | 绑定地址与端口（默认 5020；1024 以下需要权限） |
| `unit_id_mode` | `strict`：只响应已配置的站号。`any`：所有站号都路由到 `default_unit` |
| `unknown_unit` | `strict` 模式下：`ignore`（与真实总线一样不响应）或 `exception`（网关异常） |
| `default_unit` | `any` 模式以及 TCP 站号 0 / 255 使用的站号 |

## devices

| 键 | 含义 |
|---|---|
| `name`, `description` | 设备名（唯一）和说明 |
| `unit_id` | 1-247 |
| `listeners` | 设备挂接的监听器 ID（默认：全部） |
| `template`, `params` | 由模板生成点表，见[行为与模板](behaviors-templates.md) |
| `byte_order`, `word_order` | 设备默认值：`big` 或 `little` |
| `undefined_address` | 读取未定义地址时：`exception`（非法数据地址，默认）或 `zero` |
| `identification` | `vendor_name`、`product_code`、`revision`、`vendor_url`、`product_name`、`model_name` |
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

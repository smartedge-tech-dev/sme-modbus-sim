# 故障注入

故障规则让模拟器故意出错，用来测试 Modbus 主站（EMS、网关、SCADA）对超时、异常、坏帧和数值停滞的处理。
规则是**运行时状态**：在 Web UI（下方面板的 **故障注入** 页）或 REST API 中管理，不属于 YAML 配置；
重新加载配置时保留，重启后清空。

## 规则字段

| 字段 | 默认值 | 含义 |
|---|---|---|
| `id` | 自动生成（`f1`, `f2`, ...） | API 与报文追踪中使用的规则 ID |
| `name` | - | 任意名称 |
| `enabled` | `true` | |
| `listener` | 任意 | 监听器 ID |
| `unit` | 任意 | 请求的单元 ID |
| `function_codes` | 任意 | 功能码列表，如 `[3, 4]` |
| `address_start` / `address_end` | 任意 | 闭区间；与请求的 `start..start+数量-1` 有重叠即匹配（FC 23 取读或写范围） |
| `action` | - | 见下表 |
| `params` | `{}` | 动作参数 |
| `probability` | `1` | 0–1，匹配的请求触发规则的概率 |
| `every_n` | - | 每 N 个匹配请求触发一次 |
| `remaining` | - | 触发指定次数后规则自动停用 |

每个请求按列表顺序评估规则，**第一条真正触发的规则生效**。条件匹配但被 `every_n` / `probability`
判定为"本次不触发"的规则，会让给下一条规则。每次触发都会记录到追踪事件（`fault: {rule, action}`），
报文面板中以闪电符号标出。

## 动作

| 动作 | 参数 | 效果 |
|---|---|---|
| `exception` | `code`：01–06、08、0A、0B（数字或十六进制字符串，默认 04） | 返回该异常码，请求不执行 |
| `delay` | `ms`，或 `min_ms` + `max_ms`（随机） | 延迟应答；不影响其它连接 |
| `no_response` | - | 丢弃请求（不执行、不应答） |
| `disconnect` | - | TCP / RTU over TCP：收到请求后断开连接。串口 RTU：等同 `no_response` |
| `corrupt_crc` | - | RTU / RTU over TCP / 串口：两个 CRC 字节取反。Modbus TCP 不适用 |
| `truncate` | `bytes`（默认 3） | 只发送应答帧的前 N 字节 |
| `wrong_transaction_id` | - | 仅 Modbus TCP：以 transaction id + 1 应答 |
| `wrong_unit_id` | `unit`（默认：请求单元 + 1） | 以其它单元 ID 应答 |
| `busy` | `seconds`（默认 10） | 自首次触发起在指定秒数内返回异常 06，之后规则停用 |
| `freeze` | - | 匹配单元（`unit` / `listener` 条件）的点值不再由行为模型更新；Modbus 照常应答，写入照常生效 |
| `refuse_connections` | - | 监听器级（`listener` 条件）：新的 TCP 连接立即关闭 |

## Web UI

**故障注入** 页列出规则（启用开关、触发次数、删除按钮）。快捷按钮作用于侧栏中选中的设备：
**不应答**（`no_response`）、**异常 04**、**忙 10 秒**、**冻结数值**。**添加规则…** 可创建任意规则；
**导入预设…** / **导出预设** 读写 JSON 文件。

## 预设

预设是规则的 YAML / JSON 列表（或带 `faults:` 列表的映射）。请把预设另存为独立文件放在配置旁，
例如 `faults/meter-flaky.yaml`：

```yaml
- {id: slow, action: delay, params: {min_ms: 200, max_ms: 1500}, unit: 3, probability: 0.3}
- {id: crc, action: corrupt_crc, listener: gw1, every_n: 10}
- {action: exception, params: {code: "02"}, function_codes: [16], address_start: 100, address_end: 199}
```

在 CI 脚本中加载：

```bash
curl -X POST --data-binary @faults/meter-flaky.yaml http://127.0.0.1:8081/api/faults/preset
# ... 对模拟器运行测试 ...
curl -X POST http://127.0.0.1:8081/api/faults/clear
```

`POST /api/faults/preset` 替换全部规则（追加用 `?replace=false`），且全有或全无：只要有一条无效就不做任何改动。
`GET /api/faults/export` 以 `faults.json` 返回当前规则，可直接再次加载。另见 [HTTP API](http-api.md)。

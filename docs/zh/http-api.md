# HTTP API

Web UI 使用的就是这套 API，你也可以用脚本调用。所有路由都在 UI 端口（默认 8080）上提供。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | 模拟状态、监听器、设备及点值的快照 |
| GET | `/api/templates` | 可用的设备模板及其参数 |
| GET | `/api/history/{device}/{point}` | 某个点最近的采样 |
| POST | `/api/devices/{device}/points/{point}/write` | 写点，请求体 `{"value": 12.5}` |
| POST | `/api/simulation` | 暂停 / 继续并设置时间倍率：`{"running": true, "time_scale": 10}` |
| GET | `/api/config` | 以 YAML 获取当前配置 |
| PUT | `/api/config` | 替换配置（请求体为 YAML）；无效配置会连同原因一起被拒绝 |
| POST | `/api/config/save` | 把当前配置写回配置文件 |
| GET | `/api/trace` | 最近的 Modbus 请求 / 响应；查询参数 `limit`、`unit`、`fc`、`listener` |
| GET | `/api/stats` | 请求计数 |
| GET | `/api/version` | 模拟器版本 |
| WS | `/ws` | 数值变化、通信记录和事件的实时推送 |

```bash
curl -X POST http://127.0.0.1:8080/api/devices/PCS-1/points/p_setpoint/write \
     -H "Content-Type: application/json" -d '{"value": 20}'
```


## Excel 与用户模板

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/config/xlsx` | 以 `.xlsx` 获取运行中的配置 |
| `GET` | `/api/config/xlsx-template` | 空白工作簿 |
| `POST` | `/api/config/import` | 导入工作簿（multipart `file`）；出错时返回 `400` 与错误列表 |
| `POST` | `/api/templates/reload` | 重新加载用户模板目录；返回 `loaded` 与按文件的 `errors` |
| `POST` | `/api/templates/from-device/{device}` | 将设备展开后的点另存为用户模板（`{"name", "overwrite"}`）；已存在时返回 `409` |

## 故障注入

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/faults` | 规则列表 `{"rules": [...]}`（含 `triggers`、`matches`、`active`） |
| `POST` | `/api/faults` | 创建规则（JSON），返回带 `id` 的规则 |
| `PUT` | `/api/faults/{id}` | 更新规则；省略的字段保持原值 |
| `DELETE` | `/api/faults/{id}` | 删除规则 |
| `POST` | `/api/faults/clear` | 删除全部规则 |
| `POST` | `/api/faults/preset` | 加载 YAML / JSON 预设（请求体），替换全部规则，`?replace=false` 为追加 |
| `GET` | `/api/faults/export` | 以 `faults.json` 返回规则 |

变更通过 `/ws` 推送 `{"type": "faults", "rules": [...]}`。参见 [故障注入](faults.md)。

## 设备管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/devices` | 添加设备（JSON：`name`、`unit_id`，可选 `listeners`、`template`、`params`、`points` 等） |
| `POST` | `/api/devices/{name}/duplicate` | 复制设备；可选 JSON `{"name", "unit_id"}`，默认为 `{name}_copy` 和同一监听器上下一个空闲单元 ID |
| `DELETE` | `/api/devices/{name}` | 删除设备 |
| `GET` | `/api/devices/{name}/config` | 该设备的配置（YAML） |
| `PUT` | `/api/devices/{name}/config` | 用请求体中的 YAML 替换该设备（允许改名） |

所有更改都会先校验，再以与 `PUT /api/config` 相同的方式重载生效；运行中的配置由模型重新生成
（只保留显式设置的键，YAML 注释会丢失），用 `POST /api/config/save` 写回文件。
错误：`404` 设备不存在，`409` 名称已被使用，`400` 校验错误（逐条列出 pydantic 错误，格式为 `field: message`）。
参见 [设备管理](device-management.md)。

## 统计推送

只要有客户端连接，`/ws` 每 2 秒推送一次各监听器的计数：

```json
{"type": "stats", "t": 1764052800.0, "stats": {"tcp1": {"type": "tcp", "requests": {"3": 120}, "exceptions": {}, "clients": 1}}}
```

`stats` 的结构与 `GET /api/stats` 相同。

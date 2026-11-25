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

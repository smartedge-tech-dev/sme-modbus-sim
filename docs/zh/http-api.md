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

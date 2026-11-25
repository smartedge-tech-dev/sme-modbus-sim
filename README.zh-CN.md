# SME ModbusSim

[English](README.md) | [日本語](README.ja.md) | **简体中文**

一个带实时 Web UI 的 Modbus TCP / RTU 设备模拟器。可以模拟储能 PCS、BMS、电能表、环境传感器，或任何用 YAML 描述的寄存器表，并在浏览器中观察数值变化，同时由真实的 Modbus 主站（你的 EMS、网关、modpoll、pymodbus……）读写这些设备。

- **一个端口承载多台设备。** 一个监听器可服务多个站号（unit ID），一台设备也可以挂在多个监听器上。Modbus TCP 与 Modbus RTU over TCP（用于串口网关）可同时使用。
- **自研协议核心。** 支持功能码 01 / 02 / 03 / 04 / 05 / 06 / 0F / 10（十六进制），并返回标准异常响应。帧格式与 Modbus TCP / RTU 兼容（MBAP、CRC-16）。
- **数据类型齐全。** `bool`、`int16` / `uint16`、`int32` / `uint32`、`int64` / `uint64`、`float32` / `float64`、`string`、`enum`、寄存器内位，可配置字节序、字序（ABCD / CDAB / BADC / DCBA）、比例系数和偏移。
- **行为逼真。** PCS 按斜率追随功率设定值，BMS 根据 PCS 功率积分 SOC，电能表显示站内负荷减去 PCS 功率。支持正弦、随机游走、表达式、计数器、脉冲和 CSV 回放。
- **实时 UI。** 通过 WebSocket 实时更新的点表，可在浏览器中写值，每个点有历史曲线，并提供 Modbus 通信日志。界面支持英文、日文和简体中文。
- **易于配置。** 编辑 YAML，从通用模板添加设备，并可保存回配置文件。

基于为本项目编写的 asyncio 协议核心、FastAPI 和 pyserial 构建，无需前端构建步骤。

SME ModbusSim 免费且采用 MIT 许可，由 [smart-edge（智届科技）](https://smartedge-tech.com) 开发，也可承接定制开发，详见[商务支持与定制开发](#商务支持与定制开发)。

## 快速开始

### Windows exe

从 [Releases](https://github.com/smartedge-tech-dev/sme-modbus-sim/releases) 下载 `sme-modbus-sim-<version>-windows.exe` 并运行，浏览器会打开 http://127.0.0.1:8080/ 。

### Docker

```bash
docker run --rm -p 8080:8080 -p 5020:5020 -p 5021:5021 ghcr.io/smartedge-tech-dev/sme-modbus-sim
```

镜像提供 `linux/amd64`、`linux/arm64` 和 `linux/arm/v7`。每个发布版本都附带离线镜像包（`docker load -i`）。使用本地时间请加 `-e TZ=Asia/Tokyo`（默认 UTC）。使用自己的配置：`-v ./my.yaml:/app/configs/my.yaml ... -c configs/my.yaml`。

### 源码 / pip

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python -m sme_modbus_sim run -c configs/demo-site.yaml
```

打开 http://127.0.0.1:8080/ 。需要 Python 3.11 或更高版本。

| 命令 / 选项 | 含义 |
|---|---|
| `sme-modbus-sim [run]` | 启动模拟器（`run` 可省略） |
| `-c, --config FILE` | YAML 配置文件（默认 `configs/demo-site.yaml`） |
| `--host 0.0.0.0` | Web UI 绑定地址（默认仅本机） |
| `--http-port 8080` | Web UI 端口 |
| `--no-browser` | 不自动打开浏览器 |
| `validate CONFIG` | 校验配置文件后退出 |
| `templates` | 列出设备模板 |

演示配置包含 4 台设备：Modbus TCP 端口 **5020** 上的储能 PCS（站号 1）、BMS（站号 2）、并网点电能表（站号 3），以及仅能通过 RTU over TCP 监听器（端口 **5021**）访问的机房传感器（站号 4）。

## 使用 Modbus 主站测试

```bash
# modpoll（地址默认从 1 开始；加 -0 使用协议地址）
modpoll -m tcp -p 5020 -a 1 -0 -r 0 -c 4 -t 4 127.0.0.1
modpoll -m enc -p 5021 -a 4 -0 -r 0 -c 2 -t 3 127.0.0.1      # RTU over TCP
```

```python
from pymodbus.client import ModbusTcpClient

client = ModbusTcpClient("127.0.0.1", port=5020)
client.connect()
print(client.read_holding_registers(0, count=2, device_id=1).registers)
client.write_register(0, 200, device_id=1)   # PCS-1 p_setpoint = 20.0 kW（int16 x0.1）
client.close()
```

## 路线图

- **0.1.0** — Modbus TCP 与 RTU over TCP、FC 01–06 / 0F / 10、全部数据类型、YAML、Web UI、通用模板、Windows exe、Docker、CI。
- **0.2.0** — 串口 RTU（COM 口 / `/dev/ttyUSB*`）、FC 16 掩码写、FC 17 读写多个寄存器、FC 2B/0E 读设备标识、故障注入 UI（延迟、丢包、异常、错误 CRC）、Excel 点表导入导出、用户本地模板，以及由公开的 SunSpec 模型定义生成的模板。

> 与 SunSpec Alliance 无任何关联，未获其认可或认证。SunSpec 模板：`sunspec_pv_inverter`（模型 1、103、120–123、160）与 `sunspec_meter`（模型 1、203），由公开的 SunSpec 模型定义（Apache-2.0）生成。

## 商务支持与定制开发

SME ModbusSim 由 **smart-edge** —— 南京智届科技有限公司（智届科技）开发。公司成立于 2023 年，面向光伏、储能、EV 等分布式能源，开发从设备控制到云端和电力市场的端到端软件。产品包括 AERIS RA 平台（リソースアグリゲーター向け）和面向日本电力市场的 AC 系统（アグリゲーションコーディネーター）。团队可使用日语开展项目，覆盖需求定义、设计、实现、运维全过程。

服务内容：

- 能源管理系统（EMS），云端与现场
- 面向电力市场的 RA / AC 系统（JEPX、EPRX、OCCTO）
- 边缘 IoT 与设备控制软件
- 设备集成：Modbus、MQTT、BACnet、ECHONET Lite、OpenADR 2.0b
- 能源预测与数据分析
- 云基础设施的设计、搭建与运维

需要为 SME ModbusSim 增加功能，或需要对接你的设备或 EMS？欢迎联系：

- 网站：<https://smartedge-tech.com>
- 联系页面：<https://smartedge-tech.com/contact/>
- 邮箱：[business@smartedge-tech.com](mailto:business@smartedge-tech.com)

## 许可证

[MIT](LICENSE)

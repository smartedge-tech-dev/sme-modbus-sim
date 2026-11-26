# SME ModbusSim

[English](README.md) | [日本語](README.ja.md) | **简体中文**

一个带实时 Web UI 的 Modbus TCP / RTU 设备模拟器。可以模拟储能 PCS、BMS、电能表、环境传感器，或任何用 YAML 描述的寄存器表，并在浏览器中观察数值变化，同时由真实的 Modbus 主站（你的 EMS、网关、modpoll、pymodbus……）读写这些设备。

- **一个端口承载多台设备。** 一个监听器可服务多个站号（unit ID），一台设备也可以挂在多个监听器上。Modbus TCP、Modbus UDP、Modbus RTU over TCP（用于串口网关）、串口 RTU 与串口 ASCII（COM 口 / tty）可同时使用。
- **自研协议核心。** 支持功能码 01 / 02 / 03 / 04 / 05 / 06 / 0F / 10 / 16 / 17 / 2B-0E（十六进制），并返回标准异常响应；串口监听器另外支持诊断功能码 07 / 08 / 0B / 0C / 11。帧格式与 Modbus TCP / RTU 兼容（MBAP、CRC-16）。
- **数据类型齐全。** `bool`、`int16` / `uint16`、`int32` / `uint32`、`int64` / `uint64`、`float32` / `float64`、`string`、`enum`、寄存器内位，可配置字节序、字序（ABCD / CDAB / BADC / DCBA）、比例系数和偏移。
- **行为逼真。** PCS 按斜率追随功率设定值，BMS 根据 PCS 功率积分 SOC，电能表显示站内负荷减去 PCS 功率。支持正弦、随机游走、表达式、计数器、脉冲和 CSV 回放。
- **实时 UI。** 通过 WebSocket 实时更新的点表，可在浏览器中写值，每个点有历史曲线，并提供 Modbus 通信日志。界面支持英文、日文和简体中文。
- **模板。** 储能 PCS / BMS / 电能表 / 传感器通用模板、SunSpec 光伏逆变器与电能表模板、用户本地模板，以及“将设备另存为模板”。
- **故障注入。** 延迟、无响应、异常、错误 CRC、断连等，可通过 UI、REST 或预设注入。
- **易于配置。** 编辑 YAML 或 Excel 点表，在 UI 中管理设备、浏览模板，并可保存回配置文件。

基于为本项目编写的 asyncio 协议核心、FastAPI 和 pyserial 构建，无需前端构建步骤。

SME ModbusSim 免费且采用 MIT 许可，由 [smart-edge（智届科技）](https://smartedge-tech.com) 开发，也可承接定制开发，详见[商务支持与定制开发](#商务支持与定制开发)。

## 快速开始

### Windows exe

从 [Releases](https://github.com/smartedge-tech-dev/sme-modbus-sim/releases) 下载 `sme-modbus-sim-<version>-windows.exe` 并运行，浏览器会打开 http://127.0.0.1:8081/ 。

### Docker

```bash
docker run --rm -p 8081:8081 -p 5020:5020 -p 5021:5021 ghcr.io/smartedge-tech-dev/sme-modbus-sim
```

镜像提供 `linux/amd64`、`linux/arm64` 和 `linux/arm/v7`。每个发布版本都附带离线镜像包（`docker load -i`）。使用本地时间请加 `-e TZ=Asia/Tokyo`（默认 UTC）。使用自己的配置：`-v ./my.yaml:/app/configs/my.yaml ... -c configs/my.yaml`。

### 串口 RTU 与 RTU over TCP

串口 RTU 默认 19200 波特率、8E1（`type: rtu`，`port: COM5` 或 `/dev/ttyUSB0`）。RTU over TCP 在 TCP 端口上提供 RTU 帧，适用于串口网关和 RTU over TCP 模式的主站。串口 ASCII（`type: ascii`）默认 19200 波特率、7E1，1 s 内没有新字符就丢弃未完成的帧（`char_timeout`）。

```yaml
listeners:
  - {id: com1, type: rtu, port: COM5, baudrate: 19200, parity: E}   # serial RTU, 8E1
  - {id: gw1, type: rtu_over_tcp, host: 0.0.0.0, port: 5021}        # RTU over TCP
```

Docker 中用 `--device` 把串口适配器传入容器（Linux 主机；容器以非 root 用户运行，需加入设备所属组）：

```bash
docker run --rm --device /dev/ttyUSB0 --group-add dialout -p 8081:8081 \
  -v ./serial.yaml:/app/configs/serial.yaml ghcr.io/smartedge-tech-dev/sme-modbus-sim \
  python -m sme_modbus_sim run -c configs/serial.yaml --host 0.0.0.0 --no-browser
```

主站示例：`modpoll -m rtu -b 19200 -d 8 -p even -a 1 -0 -r 0 -c 4 -t 4 COM6`（串口线另一端），RTU over TCP 用 `modpoll -m enc -p 5021 -a 4 -0 -r 0 -c 2 -t 3 127.0.0.1`。

在一台 Windows 电脑上联调：Windows 没有自带的虚拟 COM 口对。可选方案：[SerialLoom](https://github.com/RahulP5/SerialLoom)（免费，MIT；基于微软签名的 usbip-win2 主控制器，COM 口由 Windows 自带的 usbser 驱动生成）、已签名的商用虚拟串口驱动，或两个 USB-RS485 转换器互接（A-A、B-B）。Windows 自带的 usbser 驱动在第一个字节到达之前就开始计字节间超时（`ReadIntervalTimeout`），所以设置了 pyserial `inter_byte_timeout` 的主站（modbus_tk 默认会设）只要应答晚于约一个系统定时器周期（约 16 ms）就会读到 0 字节；用 SerialLoom 测试时请在主站端设为 `None`（[SerialLoom#16](https://github.com/RahulP5/SerialLoom/issues/16)，[上游已知限制](https://github.com/RahulP5/SerialLoom/blob/main/docs/USB_PORTS.md#limitations)）。SerialLoom 0.4.0 起，一端的 DTR 会作为对端的 DSR 和 DCD 传过去（[SerialLoom#17](https://github.com/RahulP5/SerialLoom/issues/17)）；RTS 不传（CTS 恒为 on），BREAK 也不传。虚拟串口对不一定传 RTS / DTR，也不按波特率限速；com0com 3.0 的 64 位驱动未经正式签名，开启 Secure Boot 时无法加载。只想测 RTU 帧、不装驱动，可以用 `rtu_over_tcp` 监听器。在 Linux、macOS 和 WSL2 上，给 `rtu` 监听器指定 `port: pty`（可再加 `link: /tmp/ttyV0`），ModbusSim 会自己创建虚拟串口对，不需要驱动；也可以用 `socat -d -d pty,raw,echo=0 pty,raw,echo=0`。

### 源码 / pip

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python -m sme_modbus_sim run -c configs/demo-site.yaml
```

打开 http://127.0.0.1:8081/ 。需要 Python 3.11 或更高版本。

| 命令 / 选项 | 含义 |
|---|---|
| `sme-modbus-sim [run]` | 启动模拟器（`run` 可省略） |
| `-c, --config FILE` | YAML 配置文件（默认 `configs/demo-site.yaml`） |
| `--host 0.0.0.0` | Web UI 绑定地址（默认仅本机） |
| `--http-port 8081` | Web UI 端口 |
| `--no-browser` | 不自动打开浏览器 |
| `validate [-c] CONFIG` | 校验配置文件（含行为表达式）后退出（0 = 有效，2 = 错误） |
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

已在 0.1.0 中发布：Modbus TCP、RTU over TCP、串口 RTU；功能码
01 / 02 / 03 / 04 / 05 / 06 / 0F / 10 / 16 / 17 / 2B-0E；故障注入（UI、REST、预设）；Excel 点表导入导出；
用户本地模板与“将设备另存为模板”；通用模板以及 SunSpec 光伏逆变器 / 电能表 / DER（701-705）/ 电池（802、803）模板；真实设备的只读扫描 / 录制；带设备管理和模板浏览器的 Web UI；
Windows exe、多架构 Docker、离线包。

已在 0.2.0 中发布：串口 Modbus ASCII；内置 pty 虚拟串口对，免驱动做串口联调（Linux / macOS / WSL2）；串口诊断功能码 07 / 08 / 0B / 0C / 11。

计划：

- 0.3.0（网络）：Modbus UDP；Modbus/TCP Security（TLS）。

> 与 SunSpec Alliance 无任何关联，未获其认可或认证。`sunspec_pv_inverter`（模型 1、103、120-123、160）、`sunspec_meter`（模型 1、203）、`sunspec_der`（模型 1、701-705）与 `sunspec_battery`（模型 1、802、803）模板由公开的 SunSpec 模型定义（Apache-2.0）生成，实现 SunSpec Modbus 模型。不内置任何厂商的寄存器表；自有厂商表可通过 Excel 或用户本地模板私下导入。

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

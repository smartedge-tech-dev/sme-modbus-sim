# 快速开始

## Windows exe

从 [Releases](https://github.com/smartedge-tech-dev/sme-modbus-sim/releases) 下载
`sme-modbus-sim-<version>-windows.exe` 并运行，浏览器会打开 <http://127.0.0.1:8080/>。

## Docker

```bash
docker run --rm -p 8080:8080 -p 5020:5020 -p 5021:5021 ghcr.io/smartedge-tech-dev/sme-modbus-sim
```

镜像提供 `linux/amd64`、`linux/arm64` 和 `linux/arm/v7`。使用本地时间请加 `-e TZ=Asia/Tokyo`（默认 UTC）。
使用自己的配置：用 `-v ./my.yaml:/app/configs/my.yaml` 挂载，并在命令末尾加上
`python -m sme_modbus_sim run -c configs/my.yaml --host 0.0.0.0 --no-browser`。
容器以非 root 用户运行，因此监听端口须不小于 1024（用 5020，而不是 502）。

## 串口 RTU 与 RTU over TCP

串口 RTU 默认 19200 波特率、8E1（`type: rtu`，`port: COM5` 或 `/dev/ttyUSB0`）。RTU over TCP 在 TCP 端口上提供 RTU 帧，适用于串口网关和 RTU over TCP 模式的主站。

```yaml
listeners:
  - {id: com1, type: rtu, port: COM5, baudrate: 19200, parity: E}   # serial RTU, 8E1
  - {id: gw1, type: rtu_over_tcp, host: 0.0.0.0, port: 5021}        # RTU over TCP
```

Docker 中用 `--device` 把串口适配器传入容器（Linux 主机；容器以非 root 用户运行，需加入设备所属组）：

```bash
docker run --rm --device /dev/ttyUSB0 --group-add dialout -p 8080:8080 \
  -v ./serial.yaml:/app/configs/serial.yaml ghcr.io/smartedge-tech-dev/sme-modbus-sim \
  python -m sme_modbus_sim run -c configs/serial.yaml --host 0.0.0.0 --no-browser
```

主站示例：`modpoll -m rtu -b 19200 -d 8 -p even -a 1 -0 -r 0 -c 4 -t 4 COM6`（串口线另一端），RTU over TCP 用 `modpoll -m enc -p 5021 -a 4 -0 -r 0 -c 2 -t 3 127.0.0.1`。

## 从源码运行

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python -m sme_modbus_sim run -c configs/demo-site.yaml
```

需要 Python 3.11 或更高版本。选项：

| 命令 / 选项 | 含义 |
|---|---|
| `sme-modbus-sim [run]` | 启动模拟器（`run` 可省略） |
| `-c, --config FILE` | YAML 配置文件（默认 `configs/demo-site.yaml`） |
| `--host 0.0.0.0` | Web UI 绑定地址（默认仅本机） |
| `--http-port 8080` | Web UI 端口 |
| `--no-browser` | 不自动打开浏览器 |
| `validate CONFIG` | 校验配置文件后退出 |
| `templates` | 列出设备模板 |

## 连接 Modbus 主站

演示配置在 Modbus TCP 端口 5020（站号 1-3）和 RTU over TCP 端口 5021（站号 4）上监听。

```bash
modpoll -m tcp -p 5020 -a 1 -0 -r 0 -c 4 -t 4 127.0.0.1
modpoll -m enc -p 5021 -a 4 -0 -r 0 -c 2 -t 3 127.0.0.1
```

```python
from pymodbus.client import ModbusTcpClient

client = ModbusTcpClient("127.0.0.1", port=5020)
client.connect()
print(client.read_holding_registers(0, count=2, device_id=1).registers)
client.close()
```

配置文件中的地址是从 0 开始的协议地址；modpoll 不加 `-0` 时从 1 开始计数。

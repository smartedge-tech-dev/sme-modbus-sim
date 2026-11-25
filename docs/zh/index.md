# SME ModbusSim

一个带实时 Web UI 的 Modbus TCP / RTU 设备模拟器。可以模拟储能 PCS、BMS、电能表、环境传感器，或任何用 YAML 描述的
寄存器表，并在浏览器中观察数值变化，同时由真实的 Modbus 主站（你的 EMS、网关、modpoll、pymodbus……）读写这些设备。

- **一个端口承载多台设备。** 一个监听器可服务多个站号（unit ID），一台设备也可以挂在多个监听器上。
  Modbus TCP 与 Modbus RTU over TCP 可同时使用。
- **自研协议核心。** 支持功能码 01 / 02 / 03 / 04 / 05 / 06 / 0F / 10（十六进制），并返回标准异常响应。
- **数据类型齐全**，可配置字节序、字序、比例系数和偏移。
- **行为逼真。** 正弦、随机游走、表达式、斜率 / 一阶跟随、计数器、脉冲和 CSV 回放；
  提供储能 PCS、BMS、电能表和环境传感器的通用模板。
- **实时 UI**（英文、日文、简体中文）以及 REST / WebSocket API。

SME ModbusSim 免费且采用 MIT 许可，由 [smart-edge（智届科技）](https://smartedge-tech.com) 开发，也可承接定制开发，
详见[商务支持与定制开发](commercial-support.md)。

0.2.0 计划：串口 RTU、FC 16 / 17 / 2B-0E、故障注入 UI、Excel 点表、用户本地模板，以及由公开的 SunSpec 模型定义生成的模板（计划中）。
与 SunSpec Alliance 无任何关联，未获其认可或认证。

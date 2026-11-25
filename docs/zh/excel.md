# Excel 点表

Web UI 的 **文件** 菜单可以把运行中的配置导出为 Excel 工作簿（**导出 Excel 点表**）、下载包含全部列的空白工作簿
（**下载空白 Excel 模板**）以及导入工作簿（**导入 Excel 点表…**）。导入在应用前会完整校验；有错误时运行中的模拟器
保持不变，并在对话框中列出每条错误及其所在的表、行、列。

## 工作簿结构

| 表 | 每行表示 | 列 |
|---|---|---|
| `Listeners` | 监听器 | `id`, `type`, `host`, `port`, `unit_id_mode`, `unknown_unit`, `default_unit`, `baudrate`, `bytesize`, `parity`, `stopbits`, `extra` |
| `Devices` | 设备（unit） | `name`, `description`, `unit_id`, `listeners`, `template`, `params`, `byte_order`, `word_order`, `undefined_address`, `identification`, `extra` |
| `Points` | 点 | `device`, `table`, `address`, `name`, `description`, `datatype`, `count`, `bit`, `byte_order`, `word_order`, `scale`, `offset`, `units`, `access`, `value`, `min`, `max`, `labels`, `behavior`, `extra` |

- 第 1 行是英文列名（忽略大小写、`-` 和空格）；第 2 行以 `#` 开头，是日文 / 中文说明。第一个非空单元格以 `#` 开头的行均视为注释。
- 各列含义与[配置](configuration.md)中的同名字段相同。
- `listeners` 为逗号分隔的监听器 ID（空 = 全部监听器）。
- `params`、`identification`、`labels`、`behavior` 为 YAML flow 映射，例如 `{type: sine, min: 20, max: 26, period: 3600}`。
- `extra` 以 YAML 映射保存没有专用列的设置（例如设备的 `{removed: [alarm_grid]}`，或新版本的监听器字段），
  因此导出 → 导入可还原为等价配置。
- 带 `template` 的设备保留模板；其 `Points` 行与 YAML 的 `points:` 一样是对模板的覆盖与追加，覆盖行的 `table` 可以留空。
- 没有 `Listeners` 表（或表为空）时保留运行中的监听器。`simulation` 部分没有对应的表，始终保留运行中的设置。

## address 列

- **数字**是从 0 开始的协议地址（0〜65535），与 YAML 的 `address:` 相同。
- 首位为表前缀的 5 位或 6 位**文本**是 Modicon 记法：`0` 线圈、`1` 离散输入、`3` 输入寄存器、`4` 保持寄存器。
  `40001` 和 `400001` 都表示保持寄存器 0；5 位覆盖寄存器 1〜9999，6 位覆盖 1〜65536。
- 大于 65535 的 6 位**数字**（如 `400001`）也按 Modicon 记法读取。
- Modicon 前缀决定表；与 `table` 列不一致时报错。

!!! tip
    在 Excel 单元格中输入的 `40001` 会被存为数字，从而被解释为从 0 开始的地址 40001。要使用 Modicon 记法，请输入
    `'40001`（带撇号）或把该列设为文本格式。

## 典型流程：厂商寄存器表 → 私有模板

1. **文件 › 下载空白 Excel 模板**，把厂商寄存器表粘贴到 `Points`（设备行写一行）。
2. **文件 › 导入 Excel 点表…**，如错误对话框列出了问题行则加以修正。
3. 选中该设备，点击设备标题栏中的 **另存为模板**：其点被保存为[用户模板](behaviors-templates.md#user-local-templates)，
   之后可通过 `template:` 复用。

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/config/xlsx` | 以 `.xlsx` 获取运行中的配置 |
| `GET` | `/api/config/xlsx-template` | 空白工作簿 |
| `POST` | `/api/config/import` | multipart 字段 `file`：校验并应用。出错时返回 `400` 与 `{"detail": {"message", "errors": [...]}}` |

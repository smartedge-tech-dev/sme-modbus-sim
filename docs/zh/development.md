# 开发

```powershell
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest -q
```

## 截图

README 和文档里的截图由脚本生成，不手工截取。修改 UI 之后，重新生成三种语言的全部截图（含动图 `live.gif`）到 `docs/assets/screenshots/`；只生成一种语言可加 `--lang en`。

```powershell
.venv\Scripts\pip install playwright pillow
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python scripts\screenshots.py
```

脚本会把所有监听器和 Web UI 绑定到 `127.0.0.1`，并使用 `configs/demo-site.yaml` 的临时副本运行，不会改动你自己的配置。

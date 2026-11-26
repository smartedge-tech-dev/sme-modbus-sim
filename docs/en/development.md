# Development

```powershell
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest -q
```

## Screenshots

The README and docs screenshots are generated, not taken by hand. After a UI change, regenerate them all
(three languages, plus the animated `live.gif`) into `docs/assets/screenshots/`. Use `--lang en` for a single language.

```powershell
.venv\Scripts\pip install playwright pillow
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python scripts\screenshots.py
```

The script binds every listener and the web UI to `127.0.0.1` and runs against a temporary copy of
`configs/demo-site.yaml`, so your own configuration is never touched.

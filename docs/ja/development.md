# 開発

```powershell
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest -q
```

## スクリーンショット

README とドキュメントのスクリーンショットは手作業ではなくスクリプトで生成しています。UI を変更したら、
3 言語分（アニメーション `live.gif` を含む）を `docs/assets/screenshots/` に再生成してください。1 言語だけなら `--lang en` を付けます。

```powershell
.venv\Scripts\pip install playwright pillow
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python scripts\screenshots.py
```

スクリプトはすべてのリスナーと Web UI を `127.0.0.1` にバインドし、`configs/demo-site.yaml` の一時コピーで動作するため、
手元の設定には影響しません。

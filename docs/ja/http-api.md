# HTTP API

Web UI はこの API を利用しており、スクリプトからも呼び出せます。すべてのルートは UI のポート（既定 8080）で提供されます。

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/state` | シミュレーション、リスナー、デバイス、ポイント値のスナップショット |
| GET | `/api/templates` | 利用可能なデバイステンプレートとパラメーター |
| GET | `/api/history/{device}/{point}` | ポイントの直近のサンプル |
| POST | `/api/devices/{device}/points/{point}/write` | ポイントへ書き込み。ボディは `{"value": 12.5}` |
| POST | `/api/simulation` | 一時停止 / 再開と時間倍率: `{"running": true, "time_scale": 10}` |
| GET | `/api/config` | 現在の設定を YAML で取得 |
| PUT | `/api/config` | 設定を置き換え（ボディは YAML）。不正な場合は理由付きで拒否されます |
| POST | `/api/config/save` | 現在の設定を設定ファイルへ書き戻し |
| GET | `/api/trace` | 直近の Modbus 要求 / 応答。クエリ: `limit`, `unit`, `fc`, `listener` |
| GET | `/api/stats` | 要求カウンター |
| GET | `/api/version` | シミュレーターのバージョン |
| WS | `/ws` | 値の変化、通信ログ、イベントのライブストリーム |

```bash
curl -X POST http://127.0.0.1:8080/api/devices/PCS-1/points/p_setpoint/write \
     -H "Content-Type: application/json" -d '{"value": 20}'
```

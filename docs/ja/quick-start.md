# クイックスタート

## Windows exe

[Releases](https://github.com/smartedge-tech-dev/sme-modbus-sim/releases) から
`sme-modbus-sim-<version>-windows.exe` をダウンロードして実行します。ブラウザで <http://127.0.0.1:8080/> が開きます。

## Docker

```bash
docker run --rm -p 8080:8080 -p 5020:5020 -p 5021:5021 ghcr.io/smartedge-tech-dev/sme-modbus-sim
```

イメージは `linux/amd64`、`linux/arm64`、`linux/arm/v7` 向けです。ローカル時刻を使うには `-e TZ=Asia/Tokyo` を指定します
（既定は UTC）。独自の設定は `-v ./my.yaml:/app/configs/my.yaml` でマウントし、
`python -m sme_modbus_sim run -c configs/my.yaml --host 0.0.0.0 --no-browser` を末尾に付けて起動します。
コンテナーは root 以外のユーザーで動作するため、待ち受けポートは 1024 以上（502 ではなく 5020）にしてください。

## ソースから実行

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python -m sme_modbus_sim run -c configs/demo-site.yaml
```

Python 3.11 以上が必要です。オプション:

| コマンド / オプション | 内容 |
|---|---|
| `sme-modbus-sim [run]` | シミュレーターを起動します（`run` は省略可） |
| `-c, --config FILE` | YAML 設定ファイル（既定: `configs/demo-site.yaml`） |
| `--host 0.0.0.0` | Web UI のバインドアドレス（既定はローカルホストのみ） |
| `--http-port 8080` | Web UI のポート |
| `--no-browser` | ブラウザを開きません |
| `validate CONFIG` | 設定ファイルを検証して終了します |
| `templates` | デバイステンプレートを一覧表示します |

## Modbus マスターから接続する

デモ設定は Modbus TCP ポート 5020（ユニット 1〜3）と RTU over TCP ポート 5021（ユニット 4）で待ち受けます。

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

設定ファイルのアドレスは 0 始まりのプロトコルアドレスです。modpoll は `-0` を付けない限り 1 始まりで数えます。

# SME ModbusSim

[English](README.md) | **日本語** | [简体中文](README.zh-CN.md)

ライブ Web UI 付きの Modbus TCP / RTU デバイスシミュレーターです。蓄電池 PCS、BMS、電力量計、環境センサー、
あるいは YAML で記述した任意のレジスタマップを模擬し、実際の Modbus マスター（EMS、ゲートウェイ、modpoll、
pymodbus など）が読み書きする様子を、値の変化とともにブラウザで確認できます。

- **1 つのポートで複数デバイス。** 1 つのリスナーが複数のユニット ID を受け持ち、1 台のデバイスを複数のリスナーに
  接続することもできます。Modbus TCP と Modbus RTU over TCP（シリアルゲートウェイ向け）を同時に利用できます。
- **独自のプロトコルコア。** ファンクションコード 01 / 02 / 03 / 04 / 05 / 06 / 0F / 10（16 進） に対応し、標準の例外
  レスポンスを返します。Modbus TCP / RTU 互換のフレーミング（MBAP、CRC-16）です。
- **豊富なデータ型。** `bool`、`int16` / `uint16`、`int32` / `uint32`、`int64` / `uint64`、`float32` / `float64`、
  `string`、`enum`、レジスタ内ビットに対応し、バイトオーダー・ワードオーダー（ABCD / CDAB / BADC / DCBA）、
  スケールとオフセットを指定できます。
- **リアルな挙動。** PCS は出力指令値に向かってランプ制御で追従し、BMS は PCS の電力から SOC を積算し、
  電力量計は需要から PCS 出力を差し引いた値を示します。正弦波、ランダムウォーク、式、カウンター、パルス、
  CSV リプレイを使えます。
- **ライブ UI。** WebSocket でリアルタイム更新されるポイント一覧、ブラウザからの書き込み、ポイントごとの履歴、
  Modbus 通信ログを備えます。UI は英語・日本語・簡体字中国語です。
- **設定が容易。** YAML を編集し、汎用テンプレートからデバイスを追加し、設定ファイルへ保存できます。

本プロジェクト向けに書かれた asyncio ベースのプロトコルコア、FastAPI、pyserial で構成されています。フロントエンドの
ビルド工程はありません。

SME ModbusSim は無料の MIT ライセンスで、[smart-edge（智届科技）](https://smartedge-tech.com) が開発しています。
カスタム開発も承ります。[商用サポート・カスタム開発](#商用サポートカスタム開発) をご覧ください。

## クイックスタート

### Windows exe

[Releases](https://github.com/smartedge-tech-dev/sme-modbus-sim/releases) から `sme-modbus-sim-<version>-windows.exe`
をダウンロードして実行します。ブラウザで http://127.0.0.1:8080/ が開きます。

### Docker

```bash
docker run --rm -p 8080:8080 -p 5020:5020 -p 5021:5021 ghcr.io/smartedge-tech-dev/sme-modbus-sim
```

イメージは `linux/amd64`、`linux/arm64`、`linux/arm/v7` 向けに公開されます。オフライン用アーカイブ
（`docker load -i`）は各リリースに添付されます。ローカル時刻を使うには `-e TZ=Asia/Tokyo` を指定します（既定は UTC）。
独自の設定を使う場合: `-v ./my.yaml:/app/configs/my.yaml ... -c configs/my.yaml`。

### ソースから実行 / pip

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python -m sme_modbus_sim run -c configs/demo-site.yaml
```

http://127.0.0.1:8080/ を開きます。Python 3.11 以上が必要です。

| コマンド / オプション | 内容 |
|---|---|
| `sme-modbus-sim [run]` | シミュレーターを起動します（`run` は省略可） |
| `-c, --config FILE` | YAML 設定ファイル（既定: `configs/demo-site.yaml`） |
| `--host 0.0.0.0` | Web UI のバインドアドレス（既定はローカルホストのみ） |
| `--http-port 8080` | Web UI のポート |
| `--no-browser` | ブラウザを開きません |
| `validate CONFIG` | 設定ファイルを検証して終了します |
| `templates` | デバイステンプレートを一覧表示します |

デモ設定には 4 台のデバイスがあります。Modbus TCP ポート **5020** に蓄電池 PCS（ユニット 1）、BMS（ユニット 2）、
系統連系点の電力量計（ユニット 3）、そして RTU over TCP リスナー（ポート **5021**）経由でのみ接続できる
室内センサー（ユニット 4）です。

## Modbus マスターでのテスト

```bash
# modpoll（アドレスは既定で 1 始まり。-0 でプロトコルアドレス）
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

## ロードマップ

- **0.1.0** — Modbus TCP と RTU over TCP、FC 01〜06 / 0F / 10、全データ型、YAML、Web UI、汎用テンプレート、
  Windows exe、Docker、CI。
- **0.2.0** — シリアル RTU（COM ポート / `/dev/ttyUSB*`）、FC 16 マスク書き込み、FC 17 読み書き複合、
  FC 2B/0E 機器識別読み出し、障害注入 UI（遅延、応答破棄、例外、CRC 不正）、Excel ポイント一覧の入出力、
  ユーザーローカルテンプレート、公開されている SunSpec モデル定義から生成したテンプレート。

> SunSpec Alliance とは提携・推奨・認証の関係にありません。SunSpec テンプレート：`sunspec_pv_inverter`（モデル 1, 103, 120–123, 160）と `sunspec_meter`（モデル 1, 203）。公開されている SunSpec モデル定義（Apache-2.0）から生成しています。

## 商用サポート・カスタム開発

SME ModbusSim は **smart-edge** — 南京智届科技有限公司（智届科技）が開発しています。2023 年の創業以来、太陽光、
蓄電池、EV などの分散型エネルギーリソース向けに、機器制御からクラウド、電力市場まで一気通貫のソフトウェアを開発してきました。
製品には AERIS RA プラットフォーム（リソースアグリゲーター向け）と、日本の電力市場向けの AC システム
（アグリゲーションコーディネーター）があります。日本語で、要件定義から設計、実装、運用・保守まで対応します。

サービス:

- エネルギーマネジメントシステム（EMS）: クラウド / オンサイト
- 電力市場向け RA / AC システム（JEPX、EPRX、OCCTO）
- エッジ IoT・機器制御ソフトウェア
- 機器連携: Modbus、MQTT、BACnet、ECHONET Lite、OpenADR 2.0b
- エネルギー予測とデータ分析
- クラウドインフラの設計・構築・運用

SME ModbusSim への機能追加や、お使いのデバイスまたは EMS との連携支援が必要ですか？ お気軽にお問い合わせください:

- Web サイト: <https://smartedge-tech.com>
- お問い合わせページ: <https://smartedge-tech.com/contact/>
- E-mail: [business@smartedge-tech.com](mailto:business@smartedge-tech.com)

## ライセンス

[MIT](LICENSE)

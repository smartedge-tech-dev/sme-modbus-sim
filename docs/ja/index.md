# SME ModbusSim

ライブ Web UI 付きの Modbus TCP / RTU デバイスシミュレーターです。蓄電池 PCS、BMS、電力量計、環境センサー、
あるいは YAML で記述した任意のレジスタマップを模擬し、実際の Modbus マスター（EMS、ゲートウェイ、modpoll、
pymodbus など）が読み書きする様子を、値の変化とともにブラウザで確認できます。

- **1 つのポートで複数デバイス。** 1 つのリスナーが複数のユニット ID を受け持ち、1 台のデバイスを複数のリスナーに
  接続することもできます。Modbus TCP、Modbus UDP、Modbus RTU over TCP、シリアル RTU を同時に利用できます。
- **独自のプロトコルコア。** ファンクションコード 01 / 02 / 03 / 04 / 05 / 06 / 0F / 10 / 16 / 17 / 2B-0E（16 進）に対応し、
  標準の例外レスポンスを返します。シリアルリスナーは診断ファンクションコード 07 / 08 / 0B / 0C / 11 にも応答します。
- **豊富なデータ型。** バイトオーダー、ワードオーダー、スケール、オフセットを指定できます。
- **リアルな挙動。** 正弦波、ランダムウォーク、式、ランプ / 追従、カウンター、パルス、CSV リプレイ。
  蓄電池 PCS、BMS、電力量計、環境センサーの汎用テンプレートを備えます。
- **ライブ UI**（英語・日本語・簡体字中国語）と REST / WebSocket API。

SME ModbusSim は無料の MIT ライセンスで、[smart-edge（智届科技）](https://smartedge-tech.com) が開発しています。
カスタム開発も承ります。[商用サポート・カスタム開発](commercial-support.md) をご覧ください。

あわせて、[障害注入](faults.md)、[Excel ポイント一覧](excel.md)、ユーザーローカルテンプレートと SunSpec PV インバーター /
電力量計 / DER / 蓄電池テンプレート（[テンプレート](behaviors-templates.md)）、UI の[デバイス管理](device-management.md)、実機の読み取り専用スキャン / 記録（[クイックスタート](quick-start.md)）を備えます。
SunSpec Alliance とは提携・推奨・認証の関係にありません。SunSpec テンプレートは公開されている SunSpec モデル定義
（Apache-2.0）から生成しています。

0.2.0 でリリース済み: シリアル Modbus ASCII、ドライバー不要のシリアル試験用 pty ペア内蔵（Linux / macOS / WSL2）、シリアル診断ファンクションコード 07 / 08 / 0B / 0C / 11。

予定:

- 0.3.0（ネットワーク）: Modbus UDP、Modbus/TCP Security（TLS）。

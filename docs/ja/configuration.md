# 設定

設定ファイルは YAML で、`simulation`、`listeners`、`devices` の 3 つのセクションからなります。デモは
`configs/demo-site.yaml` です。`sme-modbus-sim validate FILE` で、起動せずにファイルを検証できます。

```yaml
simulation: {tick_seconds: 1, time_scale: 1, history_size: 600, running: true}

listeners:
  - {id: tcp1, type: tcp, host: 0.0.0.0, port: 5020, unit_id_mode: strict, unknown_unit: ignore}
  - {id: gw1, type: rtu_over_tcp, host: 0.0.0.0, port: 5021}

devices:
  - name: Meter-1
    unit_id: 3
    template: power_meter
    word_order: little
    points:
      - {name: serial_number, value: "MTR00001"}
```

## simulation

| キー | 内容 |
|---|---|
| `tick_seconds` | 挙動の更新間隔（秒。0.05 より大きく 60 以下） |
| `time_scale` | 実時間 1 秒あたりのシミュレーション秒数 （0 より大きく 3600 以下）|
| `history_size` | UI のグラフ用にポイントごとに保持するサンプル数 |
| `running` | 実行中 / 一時停止のどちらで開始するか |

## listeners

| キー | 内容 |
|---|---|
| `id` | `devices[].listeners` から参照する名前 |
| `type` | `tcp`（Modbus TCP、MBAP）、`rtu_over_tcp`（TCP ソケット上の CRC-16 付き RTU フレーム）または `rtu`（シリアル回線） |
| `host`, `port` | `tcp` / `rtu_over_tcp`: バインドアドレスと TCP ポート（既定 5020。1024 未満は権限が必要）。`rtu`: `port` はシリアルデバイス（`COM5`、`/dev/ttyUSB0`）または pyserial URL（`socket://host:port`、`rfc2217://...`）。`host` は無視 |
| `unit_id_mode` | `strict`: 設定済みのユニット ID のみ応答。`any`: すべてのユニット ID を `default_unit` に振り向ける |
| `unknown_unit` | `strict` 時の動作。`ignore`（実際のバスと同様に無応答）または `exception`（ゲートウェイ例外） |
| `default_unit` | `any` モード、および TCP のユニット ID 0 / 255 で使うユニット ID |
| `baudrate`, `bytesize`, `parity`, `stopbits` | `rtu` のみ。既定 19200（規格の既定値）、8、`E`、1。`parity`: `E` / `O` / `N`。`N` の場合の既定ストップビットは 2（Serial Line V1.02）。`N` + `stopbits: 1` は一般的だが規格外の設定として受け付ける |
| `strict_timing` | `rtu` のみ（既定 `false`）。`true`: フレーム内で t1.5 を超える間隔があれば破棄し、t3.5 ちょうどでフレーム終端とする。`false`: t1.5 を検査せず、max(t3.5, 20 ms) の無信号でフレーム終端（USB シリアル変換器はバイトをまとめて渡すため） |

### シリアル RTU（`type: rtu`）

```yaml
listeners:
  - {id: com1, type: rtu, port: COM5, baudrate: 19200, parity: E}
```

- フレーム終端 = t3.5 の無信号（11 ビット文字の 3.5 文字時間。19200 baud 超は固定 1.75 ms）。長さが分かる要求は無信号を待たずに応答する。
- ユニット ID 1〜247 は設定どおり応答。0 はブロードキャスト（書き込み系ファンクションコードをリスナー上の全デバイスで実行し、応答しない）。248〜255 は無視。
- シリアル回線では `unknown_unit: exception` は効かない（未知のユニットは常に無応答）。
- ポートを開けない場合、そのリスナーは `/api/state` で `status: error` となり、他のリスナーは動作を続ける。設定の再読み込みで再試行する。

### ファンクションコード

01、02、03、04、05、06、0F、10、16（Mask Write Register）、17（Read/Write Multiple registers、書き込み後に読み出し）、2B / 0E（Read Device Identification: 読み出しコード 01 basic、02 regular、03 extended のストリーム、04 個別。長い応答は More Follows で分割）。16 / 17 による書き込みにも 06 / 10 と同じ検査（読み取り専用ポイント、複数レジスタ値の部分書き込み、min / max）を適用する。その他のファンクションコードは例外 01 を返す。

## devices

| キー | 内容 |
|---|---|
| `name`, `description` | デバイス名（一意）と説明 |
| `unit_id` | 1〜247 |
| `listeners` | 接続するリスナー ID（既定: すべて） |
| `template`, `params` | テンプレートからポイントを生成します。[挙動とテンプレート](behaviors-templates.md) を参照 |
| `byte_order`, `word_order` | デバイスの既定値（`big` または `little`） |
| `undefined_address` | 未定義アドレスの読み出し時の動作。`exception`（不正データアドレス、既定）または `zero` |
| `identification` | `vendor_name`、`product_code`、`revision`、`vendor_url`、`product_name`、`model_name`, `user_application_name` （FC 2B / 0E で応答。既定値 `SmartEdge`、`SME-MODBUS-SIM`、プログラムのバージョン） |
| `points` | このデバイスのポイント。テンプレートのポイントと同名のエントリーは上書きになります |
| `removed` | ユーザーが削除したテンプレートのポイント |

## points

| キー | 内容 |
|---|---|
| `name`, `description`, `units` | 名称・説明・単位 |
| `table` | `coil`、`discrete`、`input`、`holding` |
| `address` | 0 始まりのプロトコルアドレス。省略時は自動割り当て |
| `datatype` | `bool`、`bit`、`uint16`、`int16`、`uint32`、`int32`、`float32`、`uint64`、`int64`、`float64`、`string`、`enum` |
| `count` | `string` のレジスタ数（1 レジスタ 2 文字） |
| `bit` | `datatype: bit` のビット番号 0〜15（holding / input レジスタの 1 ビット） |
| `byte_order`, `word_order` | デバイスの既定値を上書き |
| `scale`, `offset` | レジスタ値 = (工学値 − offset) / scale |
| `access` | `r` または `rw`。`r` のポイントへの書き込みは例外 02 を返します |
| `value` | 初期値 |
| `min`, `max` | 値の上下限 |
| `labels` | `enum` の対応表（生の値 → ラベル）。`"ON"` のようなラベルは引用符で囲んでください |
| `behavior` | [挙動とテンプレート](behaviors-templates.md) を参照 |

### バイトオーダーとワードオーダー

複数レジスタにまたがる値では、`word_order` がどのレジスタを先に送るか、`byte_order` がレジスタ内のバイト順を決めます。
float32 の 1.0（0x3F800000）は次のように送られます。

| byte / word order | レジスタ | 名称 |
|---|---|---|
| big / big | `3F80 0000` | ABCD（Modbus 標準） |
| big / little | `0000 3F80` | CDAB（電力量計で一般的） |
| little / big | `803F 0000` | BADC |
| little / little | `0000 803F` | DCBA |

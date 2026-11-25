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
| `tick_seconds` | 挙動の更新間隔（0.05〜60 秒） |
| `time_scale` | 実時間 1 秒あたりのシミュレーション秒数 |
| `history_size` | UI のグラフ用にポイントごとに保持するサンプル数 |
| `running` | 実行中 / 一時停止のどちらで開始するか |

## listeners

| キー | 内容 |
|---|---|
| `id` | `devices[].listeners` から参照する名前 |
| `type` | `tcp`（Modbus TCP、MBAP）または `rtu_over_tcp`（TCP ソケット上の CRC-16 付き RTU フレーム） |
| `host`, `port` | バインドアドレスとポート（既定 5020。1024 未満は権限が必要） |
| `unit_id_mode` | `strict`: 設定済みのユニット ID のみ応答。`any`: すべてのユニット ID を `default_unit` に振り向ける |
| `unknown_unit` | `strict` 時の動作。`ignore`（実際のバスと同様に無応答）または `exception`（ゲートウェイ例外） |
| `default_unit` | `any` モード、および TCP のユニット ID 0 / 255 で使うユニット ID |

## devices

| キー | 内容 |
|---|---|
| `name`, `description` | デバイス名（一意）と説明 |
| `unit_id` | 1〜247 |
| `listeners` | 接続するリスナー ID（既定: すべて） |
| `template`, `params` | テンプレートからポイントを生成します。[挙動とテンプレート](behaviors-templates.md) を参照 |
| `byte_order`, `word_order` | デバイスの既定値（`big` または `little`） |
| `undefined_address` | 未定義アドレスの読み出し時の動作。`exception`（不正データアドレス、既定）または `zero` |
| `identification` | `vendor_name`、`product_code`、`revision`、`vendor_url`、`product_name`、`model_name` |
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

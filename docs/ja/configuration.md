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
| `type` | `tcp`（Modbus TCP、MBAP）、`rtu_over_tcp`（TCP ソケット上の CRC-16 付き RTU フレーム）`rtu`（シリアル回線、RTU）または `ascii`（シリアル回線、Modbus ASCII） |
| `host`, `port` | `tcp` / `rtu_over_tcp`: バインドアドレスと TCP ポート（既定 5020。1024 未満は権限が必要）。`rtu` / `ascii`: `port` はシリアルデバイス（`COM5`、`/dev/ttyUSB0`）、pyserial URL（`socket://host:port`、`rfc2217://...`）、または `pty`（後述）。`host` は無視 |
| `unit_id_mode` | `strict`: 設定済みのユニット ID のみ応答。`any`: すべてのユニット ID を `default_unit` に振り向ける |
| `unknown_unit` | `strict` 時の動作。`ignore`（実際のバスと同様に無応答）または `exception`（ゲートウェイ例外） |
| `default_unit` | `any` モード、および TCP のユニット ID 0 / 255 で使うユニット ID |
| `baudrate`, `bytesize`, `parity`, `stopbits` | `rtu` / `ascii` のみ。既定 19200（規格の既定値）、8（`ascii` は 7）、`E`、1。`rtu` は 8 データビットのみ、`ascii` は 7 または 8。`parity`: `E` / `O` / `N`。`N` の場合の既定ストップビットは 2（Serial Line V1.02）。`N` + `stopbits: 1` は一般的だが規格外の設定として受け付ける |
| `strict_timing` | `rtu` のみ（既定 `false`）。`true`: フレーム内で t1.5 を超える間隔があれば破棄し、t3.5 ちょうどでフレーム終端とする。`false`: t1.5 を検査せず、max(t3.5, 20 ms) の無信号でフレーム終端（USB シリアル変換器はバイトをまとめて渡すため） |
| `char_timeout` | `ascii` のみ（既定 1 s）。この時間文字が届かなければ未完のフレームを破棄する |
| `link` | `port: pty` のみ。pty ペアのうち被試験機側の端を指すシンボリックリンクのパス（例 `/tmp/ttyV0`） |

### シリアル RTU（`type: rtu`）

```yaml
listeners:
  - {id: com1, type: rtu, port: COM5, baudrate: 19200, parity: E}
```

- フレーム終端 = t3.5 の無信号（11 ビット文字の 3.5 文字時間。19200 baud 超は固定 1.75 ms）。長さが分かる要求は無信号を待たずに応答する。
- ユニット ID 1〜247 は設定どおり応答。0 はブロードキャスト（書き込み系ファンクションコードをリスナー上の全デバイスで実行し、応答しない）。248〜255 は無視。
- シリアル回線では `unknown_unit: exception` は効かない（未知のユニットは常に無応答）。
- ポートを開けない場合、そのリスナーは `/api/state` で `status: error` となり、他のリスナーは動作を続ける。設定の再読み込みで再試行する。

### シリアル ASCII（`type: ascii`）

```yaml
listeners:
  - {id: com2, type: ascii, port: COM6}   # 19200 baud, 7E1
```

- フレーム = `:`、アドレス・PDU・LRC の 16 進文字、CR LF（Serial Line V1.02 2.5.2 節）。小文字の 16 進も受け付け、応答は大文字。
- `:` より前の文字は無視する。未完のフレームは、新しい `:` を受信したとき、または `char_timeout` を超える間隔があいたときに破棄する。LRC 不一致、16 進文字数が奇数、513 文字超のフレームには応答しない。
- `port: pty` は ASCII でも使える。アドレス指定、ブロードキャスト、障害注入は RTU と同じ。`corrupt_crc` は LRC を反転する。

### 疑似端末ペア（`port: pty`）

Linux、macOS、WSL2 では、ModbusSim 自身が仮想シリアルポートのペアを作成できます。ドライバも root も不要です。

```yaml
listeners:
  - {id: vcom, type: rtu, port: pty, link: /tmp/ttyV0}
```

- ペアの一方は ModbusSim が保持し、被試験機は `link`（またはログ・UI・`/api/state` の `device` に表示される `/dev/pts/N`）をシリアルポートとして開く。
- `link` は起動時に作成し、終了時に削除する。パスが既に存在する場合、リスナーは `status: error` となり、既存のファイルには触れない。書き込み可能なディレクトリに置くこと（`/dev` は root が必要）。
- 被試験機はいつポートを閉じて開き直してもよい。
- 制約: pty は RTS / CTS / DTR を伝えず、ボーレートとパリティは無視され、バイトの送出間隔も再現しない。t1.5 / t3.5 は試験できないため `strict_timing: false` のままにする。
- 被試験機は pty を 8N1 で開くこと。Linux（WSL2 5.15 カーネルで確認）では、pty に偶数/奇数パリティや 7 データビットを設定すると `EINVAL` で失敗する。流れるバイトは同じなので、ASCII や 8E1 のマスターもシリアル設定を 8N1 にすれば動く。
- Windows では使えない。設定検証がエラーとなり、署名済みの仮想シリアルポートドライバ、USB-RS485 変換器 2 台の相互接続、または `rtu_over_tcp` を案内する。

### ファンクションコード

01、02、03、04、05、06、0F、10、16（Mask Write Register）、17（Read/Write Multiple registers、書き込み後に読み出し）、2B / 0E（Read Device Identification: 読み出しコード 01 basic、02 regular、03 extended のストリーム、04 個別。長い応答は More Follows で分割）。シリアルリスナーは 07、08、0B、0C、11 にも応答する（後述）。16 / 17 による書き込みにも 06 / 10 と同じ検査（読み取り専用ポイント、複数レジスタ値の部分書き込み、min / max）を適用する。その他のファンクションコードは例外 01 を返す。

### シリアル診断（FC 07 / 08 / 0B / 0C / 11）

シリアルリスナー（`rtu`、`ascii`）は、仕様で「Serial Line only」とされるファンクションコードにも応答する。TCP と RTU over TCP はこれらに例外 01 を返す。

| FC | 応答 |
|---|---|
| 07 Read Exception Status | `00` |
| 08 Diagnostics | サブファンクション 00 エコー（データ長は任意）、01 通信リスタート（データ `0000` はイベントログを保持、`FF00` は消去）、02 診断レジスタ（常に 0）、03 ASCII 入力デリミタ変更、04 Listen Only Mode へ移行（応答なし）、0A カウンタクリア、0B-12 各カウンタ、14 オーバーランカウンタクリア |
| 0B Get Comm Event Counter | status `0000`（busy にはならない）とイベントカウンタ |
| 0C Get Comm Event Log | status、イベントカウンタ、バスメッセージ数、最大 64 バイトのイベント（新しい順） |
| 11 Report Server ID | デバイスの `identification.product_code` を Server ID とし、run indicator `FF`、追加データなし |

カウンタは 16 ビットで、あふれると 0 に戻り、リスナー起動時に 0 から始まる。

- リスナー（回線）単位: バスメッセージ数（08/0B、アドレスを問わず正しいフレームすべて）、バス通信エラー数（08/0C、CRC / LRC エラー、短すぎるフレーム、不正な文字、タイムアウト）、キャラクタオーバーラン数（08/12、常に 0。シミュレーターは文字を取りこぼさない）。
- リスナー上のデバイス単位: 例外数（08/0D、応答しないブロードキャストで検出したエラーも含む）、サーバーメッセージ数（08/0E、自局宛てとブロードキャスト）、無応答数（08/0F、ブロードキャストと、Listen Only Mode または `no_response` / `disconnect` 障害で応答しなかった要求）、NAK 数（08/10、例外 07）、Busy 数（08/11、例外 06。`busy` 障害など）、FC 0B のイベントカウンタ、FC 0C のイベントログ。

仕様に記述がない、または仕様間で食い違う点は次のように扱う。

- サブファンクション 05-09 は例外 03、13 および 15 以上は例外 01 を返す。
- 01 と 0A はリスナーのバスカウンタもクリアする。0A はイベントログを消さない。
- Listen Only Mode: 要求は数えてログにも残すが、実行も応答もしない。実行するのは 08/01 だけで、その場合も応答しない。移行時のイベントバイトは `04`。ブロードキャストの書き込みは Listen Only Mode のデバイスを飛ばす。
- ブロードキャストで実行するのは書き込み系ファンクションコードだけ。それ以外（診断を含む）は実行せず、例外として数える。
- イベントカウンタは例外なく完了した要求をすべて数える（ブロードキャストを含み、FC 0B と 0C 自身は除く）。
- 03 が変えるのは、リスナーが ASCII 要求の末尾で待つデリミタだけ（リスナーごとに 1 つ、配下のデバイスで共有）。応答は CR LF で終わる。01 で LF に戻る。RTU リスナーでは 03 はエコーするだけで効果はない。
- 障害注入: `exception` / `busy` 障害は本物の例外と同じく数え、`no_response` は無応答として数える。

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

## 連成サイトの例

`configs/demo-coupled-site.yaml` は、汎用テンプレートと式だけで 5 台の機器を 1 つの TCP リスナー（ポート 5020）上で連成させ、
EMS から閉ループで試験できるようにした設定です。

| 機器（unit） | ポイント | 振る舞い |
|---|---|---|
| PV-1（1、`custom`） | `p_ac` | `40 * max(0, sin(pi * (hour - 6) / 12)) * cloud_factor`（06:00〜18:00 に発電。`cloud_factor` は 0.7〜1.0 のランダムウォーク） |
| Load-1（2、`custom`） | `active_power` | `20 + 15 * exp(-((hour - 8) ** 2) / 4) + 25 * exp(-((hour - 19) ** 2) / 6) + noise(0.5)` |
| PCS-1（3、`battery_pcs`） | `p_actual` | `run_command` がオンの間、`p_setpoint` へ 10 kW/s でランプ（BMS の SOC 制限で阻止） |
| BMS-1（4、`bms`） | `soc` | レート `-p_actual / capacity_kwh * 100 / 3600`（%/秒）の counter、0〜100 にクランプ |
| Meter-1（5、`power_meter`） | `active_power` | `pv('Load-1', 'active_power') - pv('PV-1', 'p_ac') - pv('PCS-1', 'p_actual')` |

符号：PCS 電力は + 放電 / - 充電、メーター電力は + 受電 / - 逆潮流で、受電・逆潮流の電力量カウンターはこれを積算します。
振る舞いはファイル順に機器ごとに実行されるため、最後に置いたメーターは同じティックの値を使います。
試し方：unit 3 の holding 0 = 200（20.0 kW）と coil 0 = 1 を書き込むと、メーターの受電が 20 kW 下がり、BMS-1 の `soc` が減っていきます。

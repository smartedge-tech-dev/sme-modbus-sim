# Excel 点表

Web UI の **ファイル** メニューから、実行中の設定を Excel ブックとしてエクスポート（**Excel 点表をエクスポート**）、
全列を持つ空のブックをダウンロード（**空の Excel テンプレートをダウンロード**）、ブックをインポート
（**Excel 点表をインポート…**）できます。インポートは適用前にすべて検証され、エラーがあれば実行中のシミュレーターは
変更されず、シート・行・列付きのエラー一覧がダイアログに表示されます。

## ブック構成

| シート | 1 行あたり | 列 |
|---|---|---|
| `Listeners` | リスナー | `id`, `type`, `host`, `port`, `unit_id_mode`, `unknown_unit`, `default_unit`, `baudrate`, `bytesize`, `parity`, `stopbits`, `extra` |
| `Devices` | デバイス（ユニット） | `name`, `description`, `unit_id`, `listeners`, `template`, `params`, `byte_order`, `word_order`, `undefined_address`, `identification`, `extra` |
| `Points` | ポイント | `device`, `table`, `address`, `name`, `description`, `datatype`, `count`, `bit`, `byte_order`, `word_order`, `scale`, `offset`, `units`, `access`, `value`, `min`, `max`, `labels`, `behavior`, `extra` |

- 1 行目は英語の列名（大文字小文字・`-`・空白は無視）、2 行目は `#` で始まる日本語 / 中国語の説明行です。
  最初の空でないセルが `#` で始まる行はコメントとして無視されます。
- 各列の意味は[設定](configuration.md)の同名フィールドと同じです。
- `listeners` はリスナー ID のカンマ区切り（空欄＝全リスナー）。
- `params`・`identification`・`labels`・`behavior` は YAML フローマッピングです（例 `{type: sine, min: 20, max: 26, period: 3600}`）。
- `extra` は専用列のない設定を YAML マッピングで保持します（例: デバイスの `{removed: [alarm_grid]}`、新しい版のリスナー項目）。
  これによりエクスポート → インポートで同等の設定に戻ります。
- `template` を持つデバイスはそのまま保持され、その `Points` 行は YAML の `points:` と同じくテンプレートの上書き・追加です。
  上書きの行では `table` を空欄にできます。
- `Listeners` シートがない（または空）場合は実行中のリスナーを維持します。`simulation` セクションにはシートがなく、
  常に実行中の設定を維持します。

## address 列

- **数値**は 0 始まりのプロトコルアドレス（0〜65535）で、YAML の `address:` と同じです。
- 先頭桁がテーブル接頭辞である 5 桁または 6 桁の**文字列**は Modicon 記法です: `0` コイル、`1` ディスクリート入力、
  `3` 入力レジスタ、`4` 保持レジスタ。`40001` と `400001` はいずれも保持レジスタ 0。5 桁はレジスタ 1〜9999、6 桁は 1〜65536。
- 65535 を超える 6 桁の**数値**（`400001` など）も Modicon 記法として読みます。
- Modicon 接頭辞がテーブルを決めます。`table` 列と食い違う場合はエラーです。

!!! tip
    Excel はセルに入力した `40001` を数値として保存し、0 始まりのアドレス 40001 と解釈されます。Modicon 記法にするには
    `'40001`（アポストロフィ付き）と入力するか、列を文字列書式にしてください。

## 典型的な流れ: ベンダーのレジスタマップ → 個人テンプレート

1. **ファイル › 空の Excel テンプレートをダウンロード** し、ベンダーのレジスタマップを `Points` に貼り付けます（デバイス行は 1 行）。
2. **ファイル › Excel 点表をインポート…** し、エラーダイアログに出た行があれば修正します。
3. デバイスを選択し、デバイスヘッダーの **テンプレートとして保存** を押すと、ポイントが
   [ユーザーテンプレート](behaviors-templates.md#user-local-templates)として保存され、`template:` で再利用できます。

## HTTP API

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/api/config/xlsx` | 実行中の設定を `.xlsx` で取得 |
| `GET` | `/api/config/xlsx-template` | 空のブック |
| `POST` | `/api/config/import` | マルチパートの `file` を検証して適用。エラー時 `400` と `{"detail": {"message", "errors": [...]}}` |

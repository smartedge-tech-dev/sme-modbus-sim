# 挙動とテンプレート

## 挙動（behavior）

`behavior` を持つポイントは tick ごとに更新され、それ以外のポイントは最後に書き込まれた値を保持します。

| `type` | パラメーター | 結果 |
|---|---|---|
| `sine` | `min`, `max`, `period`, `phase`, `noise` | min〜max の正弦波 |
| `random` | `min`, `max` | 一様乱数 |
| `random_walk` | `min`, `max`, `step` | 範囲を制限したランダムウォーク |
| `expr` | `expr` | 同じデバイスの他のポイントを使う式（例: `temperature > 26`） |
| `follow` | `target` (point name or expression), `tau`, `noise` | 他のポイントへの一次遅れ追従 |
| `ramp` | `target`, `rate` (number or expression) | 他のポイントへの変化率制限付き追従（単位/秒） |
| `counter` | `rate`（数値または式、毎秒） | レートを積算します（例: 電力から電力量）。整数型は上限で巻き戻ります |
| `pulse` | `rate`（毎秒のパルス数） | 整数のパルスカウンター |
| `replay` | `file`, `column`, `interpolate`, `loop`, `offset` | CSV の列をシミュレーション時刻に合わせて再生 |

Modbus マスターや UI から書き込まれた値は、データ型、`min` / `max`、`access` に照らして検証されます。

## テンプレート

テンプレートはデバイスのポイントをアドレスの自動割り当て付きで生成し、`params` によってデバイス同士を連携させます
（例: PCS が対応する BMS を参照）。`template: <名前>` と `params` を指定して使い、同名のポイントをデバイス側に書くと
テンプレートの定義を上書きできます。

| テンプレート | 内容 |
|---|---|
| `battery_pcs` | 運転指令、P / Q 指令値（ランプ付き）、実績 P / Q、直流電圧、SOC 制限、状態 |
| `bms` | PCS 電力から積算した SOC、SOH、セル電圧 / 温度、アラーム |
| `power_meter` | 三相の電圧 / 電流 / 電力、周波数、電力量カウンター |
| `env_sensor` | 温度と湿度 |
| `custom` | 空のデバイス。ポイントはすべて自分で定義します |

パラメーターは `sme-modbus-sim templates` で確認できます。テンプレートは汎用の独自定義で、特定メーカーのレジスタマップは
内蔵していません。公開されている SunSpec モデル定義から生成したテンプレートは 0.2.0 で提供予定です
（SunSpec Alliance とは提携・推奨・認証の関係にありません）。


## User-local templates

自前のテンプレートはインストール先の外、YAML ファイルを置いたディレクトリに保存します:
コマンドラインの `--templates-dir DIR`、なければ `$SME_MODBUS_SIM_TEMPLATES`、なければ `~/.sme-modbus-sim/templates/`。
起動時（設定より先なので、デバイスから利用できます）と **テンプレート › ユーザーテンプレートを再読込**
（`POST /api/templates/reload`）で読み込まれます。組み込みテンプレートと同名のユーザーテンプレートは組み込みを置き換えます。
エラーのあるファイルはスキップして報告され、シミュレーターを止めることはありません。

```yaml
# ~/.sme-modbus-sim/templates/my_inverter.yaml
name: my_inverter          # 既定: ファイル名
title: My PV inverter
description: Register map of our inverter
params: {rated_kw: 10}     # 既定値。デバイスの params: で上書き
points:                    # デバイスの points: と同じスキーマ
  - {name: p_limit, table: holding, address: 0, datatype: uint16, scale: 0.1, max: "{rated_kw}"}
  - {name: p_actual, table: input, address: 0, datatype: int16, scale: 0.1,
     behavior: {type: expr, expr: "min(p_limit, {rated_kw} * 0.8)"}}
```

パラメータは**文字列**の値の中でだけ、Python の `str.format` の規則で置換されます（`{rated_kw}` → パラメータ値）。
プレースホルダー 1 つだけの文字列（`"{rated_kw}"`）は値を型ごと受け取るため、数値フィールドにも使えます。
波括弧そのものは `{{` / `}}` と書きます。

デバイスヘッダーの **テンプレートとして保存**（`POST /api/templates/from-device/{device}`、本文
`{"name": ..., "overwrite": false}`）は、デバイスの展開済みポイントをテンプレートファイルとしてこのディレクトリに書き出します。
[Excel](excel.md) でベンダーのレジスタマップを取り込んだ後などに使います。`GET /api/templates` は各テンプレートに
`source`（`builtin` / `user`）と、ユーザーテンプレートではファイルの `path` を付けて返します。

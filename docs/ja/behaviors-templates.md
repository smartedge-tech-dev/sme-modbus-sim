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

## SunSpec テンプレート

`sunspec_pv_inverter` と `sunspec_meter` は、公開されている SunSpec モデル定義（Apache-2.0、
[sunspec/models](https://github.com/sunspec/models) のコミット `90b4a331dcca1d6eac69c1bead952fddcc5852e0`
を無改変で同梱）から生成したテンプレートで、SunSpec Modbus モデルを実装しています。
SunSpec Alliance とは提携・推奨・認証の関係にありません。

| テンプレート | モデル | パラメータ |
|---|---|---|
| `sunspec_pv_inverter` | 1, 103, 120, 121, 122, 123, 160 | `rated_w`, `mppt_count`, `ac_voltage_v`, `frequency_hz`, `dc_voltage_v`, `serial_number`, `base_address` |
| `sunspec_meter` | 1, 203 | `base_load_w`, `voltage_v`, `frequency_hz`, `pv_device`, `serial_number`, `base_address` |

- `base_address`（40000 / 0 / 50000）から保持レジスタに `SunS` マーカ、各モデル（`ID`, `L`, ポイント）、
  終端モデル（`0xFFFF`, `L = 0`）を配置。ポイント名は `m<id>_<Name>`（例 `m103_W`）、繰り返しグループは
  `m160_module1_DCW` など。
- スケールファクタ：データ型 `sunspec_sf`（int16）とポイント項目 `scale_ref: <sf ポイント>`。工学値 =
  `raw x 10^sf`（sf はエンコード／デコード時に参照先から読む）。
- 未実装値（int16 / sunssf `0x8000`、uint16 `0xFFFF`、int32 `0x80000000`、uint32 `0xFFFFFFFF`、float32 NaN）は
  UI・API で `null`（ポイント項目 `nullable: true`）。値のない任意ポイントは未実装扱い。積算値は null になりません。
- インバータ：`m103_W` は日射カーブに追従し、`m123_WMaxLim_Ena = 1` のとき `m123_WMaxLimPct` で制限、
  `m123_Conn = 0` で 0。`m103_WH` は積算。メータ：`m203_W` = 負荷プロファイル − `pv_device` の `m103_W`
  （正 = 買電）、`m203_TotWhImp` / `m203_TotWhExp` は積算。

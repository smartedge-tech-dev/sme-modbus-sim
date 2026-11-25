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
内蔵していません。`sunspec_pv_inverter`、`sunspec_meter`、`sunspec_der`、`sunspec_battery` は公開されている SunSpec モデル定義（Apache-2.0）から生成した、
SunSpec Modbus モデルを実装するテンプレートです（SunSpec Alliance とは提携・推奨・認証の関係にありません）。
お手元のメーカーマップは [Excel](excel.md) またはユーザーローカルテンプレートで非公開のまま取り込めます。


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

`sunspec_pv_inverter`、`sunspec_meter`、`sunspec_der`、`sunspec_battery` は、公開されている SunSpec モデル定義（Apache-2.0、
[sunspec/models](https://github.com/sunspec/models) のコミット `90b4a331dcca1d6eac69c1bead952fddcc5852e0`
を無改変で同梱）から生成したテンプレートで、SunSpec Modbus モデルを実装しています。
SunSpec Alliance とは提携・推奨・認証の関係にありません。

| テンプレート | モデル | パラメータ |
|---|---|---|
| `sunspec_pv_inverter` | 1, 103, 120, 121, 122, 123, 160 | `rated_w`, `mppt_count`, `ac_voltage_v`, `frequency_hz`, `dc_voltage_v`, `serial_number`, `base_address` |
| `sunspec_meter` | 1, 203 | `base_load_w`, `voltage_v`, `frequency_hz`, `pv_device`, `serial_number`, `base_address` |
| `sunspec_der` | 1, 701, 702, 703, 704, 705 | `rated_w`, `ac_voltage_v`, `frequency_hz`, `vv_curves`, `vv_points`, `serial_number`, `base_address` |
| `sunspec_battery` | 1, 802, 803 | `capacity_wh`, `max_w`, `dc_voltage_v`, `initial_soc`, `string_count`, `der_device`, `serial_number`, `base_address` |

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
- DER（`sunspec_der`）：`m701_W` は日射カーブ、または `m704_WSetEna = 1` のとき `m704_WSet`（W）／
  `m704_WSetPct`（`m702_WMax` の %）へランプ追従（負 = 吸収、下限 `-m702_WChaRteMax`）。`m704_WMaxLimPctEna = 1`
  で `m704_WMaxLimPct` により制限、増加は `m704_WRmp`（毎秒 `m702_WMax` の %）で制限、`m703_ES = 0` で 0。
  `m704_VarSetEna = 1` のとき `m701_Var` は `m704_VarSetMod` に応じて `m704_VarSet` / `m704_VarSetPct` に追従。
  `m701_TotWhInj` / `m701_TotWhAbs` は積算。モデル 705 は `vv_curves` 本 × `vv_points` 点のカーブ（入れ子の繰り返し
  グループ、`NCrv` / `NPt` も連動）を持ち、例示用の既定カーブを設定。
- バッテリー（`sunspec_battery`）：`m802_W`（シミュレータの規約：正 = 放電、負 = 充電、モデル 701 `W` と同符号）は
  デモ用の日プロファイルまたは `der_device` の `m701_W` に追従し、`m802_SoCMin` / `m802_SoCMax` 到達時と
  `m802_SetOp = 2`（DISCONNECT）で停止。`m802_SoC` は `m802_W` を `m802_WHRtg` で積分。`m802_ChaSt`、`m802_V`、
  `m802_A`、セル電圧、モデル 803 のストリングポイントはそこから導出。
- 非対応（レジスタの読み書きは可能だが動作に影響しない）：復帰タイマ（`*Rvrt*`）、力率設定（`m704_PFW*`）、
  無効電力優先度、volt-var カーブの実行（`m705_Ena`、`AdptCrvReq`）、系統連系開始の閾値・遅延、単独運転防止、
  `m802_SetInvState`、ハートビート監視（`m802_CtrlHb`）、アラームリセット。詳細な意味はモデル定義に含まれないため。

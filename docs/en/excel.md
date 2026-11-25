# Excel point table

The web UI's **File** menu exports the running configuration as an Excel workbook (**Export Excel point table**),
downloads an empty workbook with all columns (**Download blank Excel template**) and imports a workbook
(**Import Excel point table…**). An import is validated completely before anything is applied; on errors the
running simulator is unchanged and a dialog lists every error with its sheet, row and column.

## Workbook layout

| Sheet | One row per | Columns |
|---|---|---|
| `Listeners` | listener | `id`, `type`, `host`, `port`, `unit_id_mode`, `unknown_unit`, `default_unit`, `baudrate`, `bytesize`, `parity`, `stopbits`, `extra` |
| `Devices` | device (unit) | `name`, `description`, `unit_id`, `listeners`, `template`, `params`, `byte_order`, `word_order`, `undefined_address`, `identification`, `extra` |
| `Points` | point | `device`, `table`, `address`, `name`, `description`, `datatype`, `count`, `bit`, `byte_order`, `word_order`, `scale`, `offset`, `units`, `access`, `value`, `min`, `max`, `labels`, `behavior`, `extra` |

- Row 1 holds the English column names (case and `-` / space are ignored); row 2 starts with `#` and describes
  each column in Japanese and Chinese. Any row whose first non-empty cell starts with `#` is a comment.
- Column meanings follow the [configuration](configuration.md) fields of the same name.
- `listeners` is a comma-separated list of listener ids (empty = all listeners).
- `params`, `identification`, `labels` and `behavior` are YAML flow mappings, for example
  `{type: sine, min: 20, max: 26, period: 3600}`.
- `extra` carries settings without a column of their own as a YAML mapping (for example `{removed: [alarm_grid]}`
  on a device, or listener fields of newer versions), so export -> import gives back an equivalent configuration.
- A device with a `template` keeps it: its `Points` rows are the template overrides and additions, exactly like
  `points:` in YAML, and `table` may be left empty for an override.
- Without a `Listeners` sheet (or with an empty one) the running listeners are kept. The `simulation` section has
  no sheet and is always kept from the running configuration.

## Address column

- A **number** is the 0-based protocol address (0 to 65535), the same as `address:` in YAML.
- **Text** of 5 or 6 digits whose first digit is a table prefix is Modicon notation: `0` coil, `1` discrete
  input, `3` input register, `4` holding register. `40001` and `400001` both mean holding register 0; 5 digits
  cover registers 1 to 9999, 6 digits 1 to 65536.
- A **number** with 6 digits above 65535 (such as `400001`) is also read as Modicon notation.
- The Modicon prefix sets the table. A different value in the `table` column is an error.

!!! tip
    Excel stores `40001` typed into a cell as a number, which is read as the 0-based address 40001. Type
    `'40001` (with an apostrophe) or format the column as text to use Modicon notation.

## Typical flow: vendor register map to private template

1. **File › Download blank Excel template**, then paste the vendor register map into `Points` (one device row).
2. **File › Import Excel point table…** and fix the rows listed in the error dialog, if any.
3. Select the device and press **Save as template** in the device header: its points are saved as a
   [user template](behaviors-templates.md#user-local-templates) and can be reused with `template:`.

## HTTP API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/config/xlsx` | Running configuration as `.xlsx` |
| `GET` | `/api/config/xlsx-template` | Blank workbook |
| `POST` | `/api/config/import` | Multipart field `file`: validate and apply. `400` with `{"detail": {"message", "errors": [...]}}` |

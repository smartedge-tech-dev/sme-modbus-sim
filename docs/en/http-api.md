# HTTP API

The web UI uses this API; you can script against it too. All routes are served on the UI port (default 8080).

| Method | Path | Description |
|---|---|---|
| GET | `/api/state` | Snapshot of simulation, listeners, devices and point values |
| GET | `/api/templates` | Available device templates and their parameters |
| GET | `/api/history/{device}/{point}` | Recent samples of a point |
| POST | `/api/devices/{device}/points/{point}/write` | Write a point, body `{"value": 12.5}` |
| POST | `/api/simulation` | Pause / resume and set the time scale: `{"running": true, "time_scale": 10}` |
| GET | `/api/config` | The current configuration as YAML |
| PUT | `/api/config` | Replace the configuration (YAML body); invalid files are rejected with the reason |
| POST | `/api/config/save` | Write the current configuration back to the config file |
| GET | `/api/trace` | Recent Modbus requests / responses; query `limit`, `unit`, `fc`, `listener` |
| GET | `/api/stats` | Request counters |
| GET | `/api/version` | Simulator version |
| WS | `/ws` | Live stream of value changes, trace entries and events |

```bash
curl -X POST http://127.0.0.1:8080/api/devices/PCS-1/points/p_setpoint/write \
     -H "Content-Type: application/json" -d '{"value": 20}'
```


## Excel and user templates

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/config/xlsx` | Running configuration as `.xlsx` |
| `GET` | `/api/config/xlsx-template` | Blank workbook |
| `POST` | `/api/config/import` | Import a workbook (multipart `file`); `400` with an error list |
| `POST` | `/api/templates/reload` | Reload the user template directory; returns `loaded` and per-file `errors` |
| `POST` | `/api/templates/from-device/{device}` | Save a device's expanded points as a user template (`{"name", "overwrite"}`); `409` if it exists |

## Fault injection

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/faults` | Rule list `{"rules": [...]}` (with `triggers`, `matches`, `active`) |
| `POST` | `/api/faults` | Create a rule (JSON); returns it with its `id` |
| `PUT` | `/api/faults/{id}` | Update a rule; omitted fields keep their value |
| `DELETE` | `/api/faults/{id}` | Delete a rule |
| `POST` | `/api/faults/clear` | Delete all rules |
| `POST` | `/api/faults/preset` | Load a YAML / JSON preset (body); replaces all rules, `?replace=false` appends |
| `GET` | `/api/faults/export` | Rules as `faults.json` |

Changes are pushed on `/ws` as `{"type": "faults", "rules": [...]}`. See [Fault injection](faults.md).

## Device management

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/devices` | Add a device (JSON: `name`, `unit_id`, optional `listeners`, `template`, `params`, `points`, ...) |
| `POST` | `/api/devices/{name}/duplicate` | Copy a device; optional JSON `{"name", "unit_id"}`, defaults `{name}_copy` and the next free unit id on the same listeners |
| `DELETE` | `/api/devices/{name}` | Delete a device |
| `GET` | `/api/devices/{name}/config` | That device's configuration as YAML |
| `PUT` | `/api/devices/{name}/config` | Replace that device with the YAML body (a rename is allowed) |

Every change is validated and applied with the same reload as `PUT /api/config`; the running configuration is
re-written from the model (only the keys that were set are kept, YAML comments are lost) and
`POST /api/config/save` writes it to the file. Errors: `404` unknown device, `409` name already used,
`400` validation error (the message lists each pydantic error as `field: message`). See [Device management](device-management.md).

## Statistics push

While at least one client is connected, `/ws` pushes the per-listener counters every 2 s:

```json
{"type": "stats", "t": 1764052800.0, "stats": {"tcp1": {"type": "tcp", "requests": {"3": 120}, "exceptions": {}, "clients": 1}}}
```

`stats` has the same shape as `GET /api/stats`.

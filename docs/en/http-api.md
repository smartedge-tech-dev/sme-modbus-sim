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

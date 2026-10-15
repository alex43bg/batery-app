# Battery backend

Standalone FastAPI service for the battery monitor. It stores one shared battery
state as JSON and sends a Home Assistant webhook when the estimated charge
crosses the configured threshold while discharging. The webhook is retried if
Home Assistant cannot be reached. The alert is re-armed after the battery rises
above the threshold.

## Run with Docker Compose

The single container serves both the frontend and API from the same address.
From this directory:

```powershell
Copy-Item setup.env.example setup.env
# Set API_TOKEN to a value of at least 5 characters, and set
# HA_WEBHOOK_URL to Home Assistant's webhook URL.
docker compose --env-file setup.env up -d --build
```

The app listens on host port `13013` by default (configurable with
`BACKEND_PORT`). Open `http://<server-ip>:13013/` for the frontend and
`http://<server-ip>:13013/api/health` for its health check. The browser uses
the same origin for API requests, so a separate frontend container and
cross-origin configuration are not needed.
Port `130130` is not valid: TCP/UDP ports range from 1 to 65535.
Compose loads application settings from `setup.env`. Pass `--env-file setup.env`
as shown above so Compose also uses its `BACKEND_PORT` value for port mapping.

`GET` and `PUT /api/state` require `Authorization: Bearer <API_TOKEN>`. The token
is not returned by the API. Keep it private and use HTTPS or a trusted VPN when
connecting over an untrusted network; the sample direct-IP HTTP URL is suitable
only for local/private-network setup.

The named `battery-state` volume keeps `state.json` across container recreation
and server restarts; do not remove the volume if you need to retain the state.
The JSON file is atomically replaced and flushed to disk on updates. Its saved
timestamp is used after a restart to advance the estimated percentage through
the time the server was offline, while preserving the operating mode and
settings. This service is intended to run as a single container instance.

## API

- `GET /api/health` — liveness check.
- `GET /api/state` — current state, with its estimated percentage advanced to now.
- `PUT /api/state` — replace the battery settings and current percentage.
- `POST /api/webhook/test` — send an empty `POST` request to the configured Home Assistant webhook, matching the standalone webhook test page (requires the bearer token).

Example update:

```json
{
  "percent": 48,
  "mode": "discharging",
  "capacity": 192,
  "charge_hours": 2.5,
  "load_rate": 4,
  "charge_watts": 97
}
```

Set `ALERT_THRESHOLD_PERCENT` (default `30`) and `HA_WEBHOOK_URL` in `setup.env`.
The automatic alert and manual test both send an empty `POST`, matching the
standalone webhook test page. If no webhook URL is configured, the alert remains
pending and is delivered after a URL is configured and the container restarts.

When the battery is discharging and reaches the configured alert threshold, the
service sends the same empty `POST` as the manual webhook test. After Home
Assistant accepts it, the shared battery mode switches to charging. The
threshold is stored with the shared state and can be changed from the frontend.

After charging reaches 100%, the backend switches the shared mode back to
discharging after the configured `full_charge_discharge_delay_minutes`
(default `10`; `0` switches immediately). The delay is stored in the shared
state and is editable in the frontend settings.

`CORS_ORIGINS` is only needed when another origin accesses the API. For the
bundled same-origin frontend, it does not need to be configured. CORS is not
authentication. Do not expose the API directly to the public internet over
plain HTTP.

## Frontend connection

For a separate frontend deployment, set `window.BATTERY_API_URL` before the
page's main script to the backend URL and allow the frontend origin in
`CORS_ORIGINS`. With the bundled frontend, open `http://<server-ip>:13013/`
and enter the `API_TOKEN` value from `setup.env` in Settings.

## Tests

```powershell
python -m unittest discover -s tests
```

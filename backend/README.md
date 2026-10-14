# Battery backend

Standalone FastAPI service for the battery monitor. It stores one shared battery
state as JSON and sends a Home Assistant webhook when the estimated charge
crosses the configured threshold while discharging. The webhook is retried if
Home Assistant cannot be reached. The alert is re-armed after the battery rises
above the threshold.

## Run with Docker Compose

From this directory:

```powershell
Copy-Item .env.example .env
# Set API_TOKEN to a random value of at least 32 characters, and set
# HA_WEBHOOK_URL to Home Assistant's webhook URL.
docker compose up -d --build
```

The API listens on container port `8000`; the host port defaults to `13013` and
can be changed with `BACKEND_PORT`. For example, `http://<server-ip>:13013/api/health`.
Port `130130` is not valid: TCP/UDP ports range from 1 to 65535.

`GET` and `PUT /api/state` require `Authorization: Bearer <API_TOKEN>`. The token
is not returned by the API. Keep it private and use HTTPS or a trusted VPN when
connecting over an untrusted network; the sample direct-IP HTTP URL is suitable
only for local/private-network setup.

The named `battery-state` volume keeps `state.json` across container recreation.
The JSON file is atomically replaced on updates; this service is intended to run
as a single container instance.

## API

- `GET /api/health` — liveness check.
- `GET /api/state` — current state, with its estimated percentage advanced to now.
- `PUT /api/state` — replace the battery settings and current percentage.

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

Set `ALERT_THRESHOLD_PERCENT` (default `30`) and `HA_WEBHOOK_URL` in `.env`.
The webhook receives a JSON `POST` containing `event`, `percent`,
`threshold_percent`, and `created_at_ms`. If no webhook URL is configured, the
alert remains pending and is delivered after a URL is configured and the
container restarts.

`CORS_ORIGINS` is a comma-separated list of allowed frontend origins. Its
default `*` is convenient for initial setup only; CORS is not authentication.
Set it to the exact frontend origin for production. Do not expose the API
directly to the public internet over plain HTTP.

## Frontend connection

Set `window.BATTERY_API_URL` before the page's main script to the backend's
HTTPS URL, and set `CORS_ORIGINS` to the frontend's origin. In the page's
settings dialog, enter the `API_TOKEN` value from `.env`; it is stored in that
browser's local storage. Serve the frontend over HTTPS with an HTTPS API
endpoint to avoid browser mixed-content restrictions.

## Tests

```powershell
python -m unittest discover -s tests
```

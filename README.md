# Battery Monitor

Battery charge monitor with a standalone FastAPI backend and a static HTML
frontend. The backend stores shared state in a JSON file and can send a
Home Assistant webhook when the estimated charge crosses the configured
threshold while discharging.

## Local development

Create `backend/setup.env` from `backend/setup.env.example` if it does not
exist, and set `API_TOKEN` to a value of at least 5 characters. Then
run the backend in PowerShell:

```powershell
Set-Location 'K:\A_SERVER_CONFIG\battery-monitor\backend'
.\run-local.ps1
```

`run-local.ps1` reads `API_TOKEN`, `HA_WEBHOOK_URL`, and
`ALERT_THRESHOLD_PERCENT` from `setup.env`, serves the API on port `8000` for
local-network devices, and stores state in
`%LOCALAPPDATA%\BatteryMonitor\state.json` so it survives restarts without
triggering workspace file watchers. Previously saved state is copied there on
first launch. It prints the computer's private network addresses and allows
those addresses as frontend CORS origins. Local tests can send real
notifications to Home Assistant; an already-pending alert
is delivered when the backend starts. It stops with a clear message if another
process already uses port `8000`. The backend also serves the frontend on
`http://<computer-ip>:8000/`. Alternatively, in a second PowerShell window,
serve `index.html` from the repository root for frontend development:

```powershell
py -m http.server 5500 --bind 0.0.0.0
```

Open the printed `http://<computer-ip>:5500/index.html` address on a phone
connected to the same trusted Wi-Fi network. The frontend automatically uses
the same computer IP on port `8000` for the API. Enter the `API_TOKEN` from
`backend/setup.env` in **Settings** and select **Connect server**. Change the
automatic charging threshold by selecting its percentage in the battery level
forecast. When the battery reaches that threshold while discharging, the
backend sends the Home Assistant webhook; after it succeeds, the app switches
to charging mode.

In Settings, configure the whole-minute delay after charging reaches 100%
before the backend automatically switches back to discharging. The default is
10 minutes; set it to `0` to switch immediately. This transition is shared
across connected pages and remains active even if no page is open, as long as
the backend is running.

Pages connected with the same API token share the backend's battery level,
operating mode, settings, and automatic charging threshold. Visible pages
refresh shared changes every five seconds; local changes are sent immediately
without periodic stale writes.

If the API still cannot be reached from a phone, allow inbound TCP port `8000`
in Windows Firewall for the trusted private network only. Do not expose this
HTTP API to the public internet.

## Docker deployment

See [`backend/README.md`](backend/README.md). Configure `API_TOKEN`,
`HA_WEBHOOK_URL`, and `CORS_ORIGINS` in `backend/setup.env`, then run Docker Compose
from the `backend` directory. The persistent JSON state is stored in a named
Docker volume. The same container serves both the app and API at
`http://<server-ip>:13013/` by default.

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Never commit `setup.env` files, API tokens, or Home Assistant webhook URLs.

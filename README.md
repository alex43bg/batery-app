# Battery Monitor

Battery charge monitor with a standalone FastAPI backend and a static HTML
frontend. The backend stores shared state in a JSON file and can send a
Home Assistant webhook when the estimated charge crosses the configured
threshold while discharging.

## Local development

Create `backend/.env` from `backend/.env.example` if it does not exist, and set
`API_TOKEN` to a random value of at least 32 characters. Then run the backend
in PowerShell:

```powershell
Set-Location 'K:\A_SERVER_CONFIG\battery-monitor\backend'
.\run-local.ps1
```

`run-local.ps1` reads `API_TOKEN` from `.env`, listens on `127.0.0.1:8000`,
reads `HA_WEBHOOK_URL` from `.env`, and stores test state in the system
temporary directory. Local tests can send real notifications to Home Assistant;
an already-pending alert is delivered when the backend starts. It stops with a
clear message if another process already uses port `8000`. In a second
PowerShell window, serve `index.html` from the repository root:

```powershell
py -m http.server 5500 --bind 127.0.0.1
```

Open <http://127.0.0.1:5500/index.html>, enter the `API_TOKEN` from
`backend/.env` in **Settings**, and select **Connect server**. Change the
automatic charging threshold by selecting its percentage in the battery level
forecast. When the battery reaches that threshold while discharging, the
backend sends the Home Assistant webhook; after it succeeds, the app switches
to charging mode.

## Docker deployment

See [`backend/README.md`](backend/README.md). Configure `API_TOKEN`,
`HA_WEBHOOK_URL`, and `CORS_ORIGINS` in `backend/.env`, then run Docker Compose
from the `backend` directory. The persistent JSON state is stored in a named
Docker volume.

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Never commit `.env` files, API tokens, or Home Assistant webhook URLs.

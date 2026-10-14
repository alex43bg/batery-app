# Battery Monitor

Battery charge monitor with a standalone FastAPI backend and a static HTML
frontend. The backend stores shared state in a JSON file and can send a
Home Assistant webhook when the estimated charge crosses the configured
threshold while discharging.

## Local development

Run these commands in PowerShell from the `backend` directory:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:API_TOKEN = "replace-with-a-random-secret-at-least-32-characters"
$env:STATE_FILE = Join-Path $env:TEMP "battery-local-state.json"
$env:HA_WEBHOOK_URL = ""
$env:CORS_ORIGINS = "http://127.0.0.1:5500"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Serve `index.html` in a second PowerShell window from the repository root:

```powershell
py -m http.server 5500 --bind 127.0.0.1
```

Open <http://127.0.0.1:5500/index.html>, enter the same API token in
**Settings**, and select **Connect server**. The local webhook is intentionally
disabled in this example.

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

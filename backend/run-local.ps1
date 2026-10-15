$ErrorActionPreference = "Stop"

$envFile = Join-Path $PSScriptRoot "setup.env"
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Missing $envFile. Create it from setup.env.example and set API_TOKEN."
}

$tokenLine = Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match '^\s*API_TOKEN\s*=' } |
    Select-Object -First 1
$webhookLine = Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match '^\s*HA_WEBHOOK_URL\s*=' } |
    Select-Object -First 1
$alertThresholdLine = Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match '^\s*ALERT_THRESHOLD_PERCENT\s*=' } |
    Select-Object -First 1
if (-not $tokenLine) {
    throw "API_TOKEN is missing from $envFile."
}
if (-not $webhookLine) {
    throw "HA_WEBHOOK_URL is missing from $envFile."
}

$apiToken = ($tokenLine -replace '^\s*API_TOKEN\s*=', '').Trim()
$webhookUrl = ($webhookLine -replace '^\s*HA_WEBHOOK_URL\s*=', '').Trim()
if (
    $apiToken.Length -ge 2 -and
    (($apiToken[0] -eq '"' -and $apiToken[-1] -eq '"') -or
        ($apiToken[0] -eq "'" -and $apiToken[-1] -eq "'"))
) {
    $apiToken = $apiToken.Substring(1, $apiToken.Length - 2)
}
if (
    $webhookUrl.Length -ge 2 -and
    (($webhookUrl[0] -eq '"' -and $webhookUrl[-1] -eq '"') -or
        ($webhookUrl[0] -eq "'" -and $webhookUrl[-1] -eq "'"))
) {
    $webhookUrl = $webhookUrl.Substring(1, $webhookUrl.Length - 2)
}
$alertThresholdPercent = "30"
if ($alertThresholdLine) {
    $alertThresholdPercent = ($alertThresholdLine -replace '^\s*ALERT_THRESHOLD_PERCENT\s*=', '').Trim()
    if (
        $alertThresholdPercent.Length -ge 2 -and
        (($alertThresholdPercent[0] -eq '"' -and $alertThresholdPercent[-1] -eq '"') -or
            ($alertThresholdPercent[0] -eq "'" -and $alertThresholdPercent[-1] -eq "'"))
    ) {
        $alertThresholdPercent = $alertThresholdPercent.Substring(1, $alertThresholdPercent.Length - 2)
    }
    $parsedAlertThresholdPercent = 0.0
    $isValidAlertThreshold = [double]::TryParse(
        $alertThresholdPercent,
        [Globalization.NumberStyles]::Float,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsedAlertThresholdPercent
    )
    if (-not $isValidAlertThreshold -or $parsedAlertThresholdPercent -lt 0 -or $parsedAlertThresholdPercent -gt 100) {
        throw "ALERT_THRESHOLD_PERCENT in $envFile must be between 0 and 100."
    }
}
if ($apiToken.Length -lt 5) {
    throw "API_TOKEN in $envFile must contain at least 5 characters."
}
if (
    -not [string]::IsNullOrWhiteSpace($webhookUrl) -and
    (
        -not [uri]::IsWellFormedUriString($webhookUrl, [System.UriKind]::Absolute) -or
        $webhookUrl -notmatch '^https?://'
    )
) {
    throw "HA_WEBHOOK_URL in $envFile must be an absolute HTTP or HTTPS URL."
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment not found at $python. Create it and install requirements.txt."
}

$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    throw "Port 8000 is already in use by PID $($listener.OwningProcess). Stop the existing backend with Ctrl+C before starting this one."
}

$env:API_TOKEN = $apiToken
$env:ALERT_THRESHOLD_PERCENT = $alertThresholdPercent
$stateDirectory = Join-Path $env:LOCALAPPDATA "BatteryMonitor"
$env:STATE_FILE = Join-Path $stateDirectory "state.json"
if (-not (Test-Path -LiteralPath $env:STATE_FILE -PathType Leaf)) {
    $previousStateFiles = @(
        (Join-Path $PSScriptRoot "data\state.json"),
        (Join-Path $env:TEMP "battery-local-state.json")
    )
    $previousStateFile = $previousStateFiles |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if ($previousStateFile) {
        New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
        Copy-Item -LiteralPath $previousStateFile -Destination $env:STATE_FILE
        Write-Host "Restored saved battery state from $previousStateFile"
    }
}
$env:HA_WEBHOOK_URL = $webhookUrl
$frontendOrigins = [System.Collections.Generic.List[string]]::new()
$frontendOrigins.Add("http://127.0.0.1:5500")
$frontendOrigins.Add("http://localhost:5500")
$lanAddresses = [System.Collections.Generic.List[string]]::new()
foreach ($configuration in Get-NetIPConfiguration) {
    if ($configuration.NetAdapter.Status -ne "Up") {
        continue
    }

    foreach ($address in $configuration.IPv4Address) {
        $octets = $address.IPAddress.Split(".") | ForEach-Object { [int]$_ }
        $isPrivate = (
            $octets[0] -eq 10 -or
            ($octets[0] -eq 172 -and $octets[1] -ge 16 -and $octets[1] -le 31) -or
            ($octets[0] -eq 192 -and $octets[1] -eq 168)
        )
        if ($isPrivate) {
            $lanAddresses.Add($address.IPAddress)
            $frontendOrigins.Add("http://$($address.IPAddress):5500")
        }
    }
}
$env:CORS_ORIGINS = ($frontendOrigins | Sort-Object -Unique) -join ","

Push-Location $PSScriptRoot
try {
    foreach ($address in ($lanAddresses | Sort-Object -Unique)) {
        Write-Host "Backend available at http://${address}:8000"
        Write-Host "Frontend should be available at http://${address}:5500/index.html"
    }
    & $python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    if ($LASTEXITCODE -ne 0) {
        throw "Uvicorn exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

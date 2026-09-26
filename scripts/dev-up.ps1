<#
.SYNOPSIS
  Start everything for testing the app on your phone over Wi-Fi: database, API, worker, Expo.

.DESCRIPTION
  Safe to rerun: it restarts the windows it opened before (e.g. after editing .env).
  Opens a window per service (close a window to stop that service):
    - "SG Events API"    FastAPI on 0.0.0.0 so the phone can reach it (admin console stays PC-only)
    - "SG Events worker" scheduled crawls + enrichment; crawls once at start with -Crawl
    - "SG Events Expo"   the Expo dev server: scan its QR code with Expo Go
  and opens the admin console in your browser.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1
  powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Crawl
#>
param(
  [int]$ApiPort = 9000,
  [int]$MetroPort = 8081,
  [switch]$Crawl,
  [switch]$NoWorker
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Get-ReservedRanges {
  # Hyper-V/WSL reserve TCP port ranges that can't be bound; they change after reboots.
  netsh interface ipv4 show excludedportrange protocol=tcp |
    Select-String '^\s+(\d+)\s+(\d+)' |
    ForEach-Object { , @([int]$_.Matches[0].Groups[1].Value, [int]$_.Matches[0].Groups[2].Value) }
}

function Test-PortUsable([int]$port) {
  foreach ($r in Get-ReservedRanges) { if ($port -ge $r[0] -and $port -le $r[1]) { return $false } }
  return -not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Find-Port([int]$preferred, [int[]]$fallbacks) {
  foreach ($p in @($preferred) + $fallbacks) { if (Test-PortUsable $p) { return $p } }
  throw "No usable port among $preferred, $($fallbacks -join ', ')"
}

function Test-Api([string]$url) {
  try { (Invoke-WebRequest -UseBasicParsing "$url/health" -TimeoutSec 3).StatusCode -eq 200 } catch { $false }
}

function Stop-ServiceWindow([string]$title) {
  # Windows this script opened earlier, found by the title set in their command line.
  Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -like "*WindowTitle = '$title'*" } |
    ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null; Write-Host "Stopped the previous '$title' window." -ForegroundColor Yellow }
}

function Start-ServiceWindow([string]$title, [string]$dir, [string]$command) {
  Stop-ServiceWindow $title  # rerunning the script restarts services instead of duplicating them
  $script = "`$host.UI.RawUI.WindowTitle = '$title'; Set-Location '$dir'; $command"
  Start-Process powershell -ArgumentList @('-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $script) | Out-Null
}

# --- where the phone should connect --------------------------------------------------------------
$lan = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
if (-not $lan) { throw 'No active network with a gateway: connect the PC to Wi-Fi first.' }
$ip = $lan.IPv4Address.IPAddress
$netProfile = (Get-NetConnectionProfile -InterfaceIndex $lan.InterfaceIndex).NetworkCategory
if ($netProfile -ne 'Private') {
  Write-Warning "This network is '$netProfile'. Windows blocks incoming connections on Public networks: set it to Private (Settings > Network > Wi-Fi > your network)."
}

# --- database + schema ---------------------------------------------------------------------------
Write-Host 'Starting the database...' -ForegroundColor Cyan
Push-Location $root
docker compose up -d --wait | Out-Null
Pop-Location
uv --directory "$root\backend" run alembic upgrade head

# --- API -----------------------------------------------------------------------------------------
Stop-ServiceWindow 'SG Events API'  # restart ours so new code and .env changes take effect
Start-Sleep -Seconds 1
$apiLocal = "http://127.0.0.1:$ApiPort"
if (Test-Api $apiLocal) {
  Write-Host "An API not started by this script is running on port $ApiPort; using it." -ForegroundColor Yellow
} else {
  $ApiPort = Find-Port $ApiPort @(9100, 9200, 18000)
  $apiLocal = "http://127.0.0.1:$ApiPort"
  Start-ServiceWindow 'SG Events API' "$root\backend" "uv run uvicorn app.main:app --host 0.0.0.0 --port $ApiPort"
  Write-Host "Starting the API on port $ApiPort..." -ForegroundColor Cyan
  foreach ($i in 1..60) { if (Test-Api $apiLocal) { break }; Start-Sleep -Milliseconds 500 }
  if (-not (Test-Api $apiLocal)) { throw 'The API did not start: check the "SG Events API" window.' }
}
$apiLan = "http://${ip}:$ApiPort"

# --- worker --------------------------------------------------------------------------------------
if (-not $NoWorker) {
  $now = if ($Crawl) { ' --now' } else { '' }
  Start-ServiceWindow 'SG Events worker' "$root\backend" "uv run python -m app.worker$now"
}

# --- Expo ----------------------------------------------------------------------------------------
Set-Content -Path "$root\mobile\.env.local" -Value "EXPO_PUBLIC_API_URL=$apiLan" -Encoding ascii
Stop-ServiceWindow 'SG Events Expo'
# give a stopped Metro a moment to release its port, so the QR address stays the same
foreach ($i in 1..20) { if (Test-PortUsable $MetroPort) { break }; Start-Sleep -Milliseconds 500 }
$MetroPort = Find-Port $MetroPort @(8300, 8400, 19000, 19006)
Start-ServiceWindow 'SG Events Expo' "$root\mobile" "npx expo start --lan --port $MetroPort"

Start-Process "$apiLocal/admin"

Write-Host ''
Write-Host 'Ready to test' -ForegroundColor Green
Write-Host "  API for the phone:  $apiLan   (docs: $apiLocal/docs)"
Write-Host "  Admin console:      $apiLocal/admin   (this PC only)"
Write-Host "  Expo:               exp://${ip}:$MetroPort   (QR code in the 'SG Events Expo' window)"
Write-Host ''
Write-Host 'On your phone (same Wi-Fi): install Expo Go, then scan the QR code (iPhone: Camera app).'
Write-Host 'If Windows asks whether Python or Node may use the network, allow Private networks.'
Write-Host 'To stop: close the service windows (the database keeps running; docker compose stop).'

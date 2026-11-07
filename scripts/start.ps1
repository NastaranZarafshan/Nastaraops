# Runs infra in Docker and Python services in the host .venv. Windows PowerShell 5.1+.
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$root = (Get-Location).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Run scripts/setup.ps1 first: Python packages are installed locally in .venv.' }
if (-not (Test-Path '.env')) { throw 'Missing .env. Run scripts/setup.ps1 first.' }
& $python -c 'import fastapi, asyncpg, redis, mcp'
if ($LASTEXITCODE -ne 0) { throw 'Python libraries are missing. Rerun scripts/setup.ps1.' }
& docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop Engine is unavailable.' }

$local = Join-Path $root '.local'
New-Item -ItemType Directory -Path $local -Force | Out-Null
$processFile = Join-Path $local 'processes.json'
if (Test-Path $processFile) {
    throw 'NastaraOps already has a process record. Run scripts/stop.ps1 before starting it again.'
}
$portLine = Get-Content '.env' | Where-Object { $_ -match '^NASTARAOPS_PORT=' } | Select-Object -Last 1
$port = if ($portLine) { [int](($portLine -split '=',2)[1]) } else { 8010 }
function Test-PortInUse([int]$value) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $result = $client.BeginConnect('127.0.0.1',$value,$null,$null); if ($result.AsyncWaitHandle.WaitOne(150)) { $client.EndConnect($result); return $true }; return $false }
    catch { return $false }
    finally { $client.Close() }
}
foreach ($p in @($port,8001)) { if (Test-PortInUse $p) { throw "Local port $p is already in use. Stop that process or adjust the port before starting." } }
Write-Host 'Starting isolated PostgreSQL / Redis / Qdrant in Docker...' -ForegroundColor Cyan
& docker compose up -d
if ($LASTEXITCODE -ne 0) { throw 'Docker infrastructure failed. Do not delete volumes; review docker compose logs.' }

# Wait for postgres and redis healthy; Qdrant readiness verified by API /ready.
$healthy = $false
for ($i=0; $i -lt 40; $i++) {
    $pg = (& docker compose ps --format json postgres | Out-String)
    $rd = (& docker compose ps --format json redis | Out-String)
    if ($pg -match 'healthy' -and $rd -match 'healthy') { $healthy = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $healthy) { throw 'Database/Redis not healthy. Check: docker compose ps; docker compose logs --tail=70 postgres redis' }

# Python's CREATE_NO_WINDOW prevents extra Windows Terminal windows.
# The helper saves the exact process IDs for scripts/stop.ps1 and redirects service logs.
try {
    & $python (Join-Path $root 'scripts\launch_services.py') $port
    if ($LASTEXITCODE -ne 0) { throw 'Python services could not be launched. Check .local/*.stderr.log.' }
    $healthy = $false
    for ($i=0; $i -lt 40; $i++) {
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
            if ($status.status -eq 'ok') { $healthy = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $healthy) {
        Write-Host 'API startup failed; last stderr lines:' -ForegroundColor Yellow
        Get-Content (Join-Path $local 'api.stderr.log') -Tail 45
        throw 'API did not become healthy. Run scripts/stop.ps1 before retrying.'
    }
    Write-Host ''
    Write-Host "NastaraOps is running: http://127.0.0.1:$port" -ForegroundColor Green
    Write-Host "Swagger UI: http://127.0.0.1:$port/docs"
    Write-Host 'Use the API_KEY in your local .env. Logs: .local/*.log'
    Write-Host 'To stop: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop.ps1'
    try { $ready = Invoke-RestMethod -Uri "http://127.0.0.1:$port/ready" -TimeoutSec 6; Write-Host ('Dependencies: ' + $ready.status) }
    catch { Write-Host 'Some dependencies are not ready. Check docker compose ps and .local logs.' -ForegroundColor Yellow }
} catch { throw }

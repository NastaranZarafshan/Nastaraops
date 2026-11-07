# NastaraOps - safely stop locally launched Python processes and infrastructure.
# This script does not delete Docker volumes, .env, or any project files.
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$root = (Get-Location).Path
$record = Join-Path $root '.local\processes.json'
$expectedPython = Join-Path $root '.venv\Scripts\python.exe'

if (Test-Path -LiteralPath $record) {
    try {
        $json = Get-Content -LiteralPath $record -Raw -Encoding UTF8
        $decoded = ConvertFrom-Json -InputObject $json
    } catch {
        throw 'Cannot read .local/processes.json. The file was preserved for inspection.'
    }

    # Windows PowerShell can wrap JSON arrays as a single pipeline object.
    # Explicitly flatten the decoded collection before reading PID values.
    $rows = @()
    foreach ($item in $decoded) {
        if ($item -is [System.Array]) {
            foreach ($entry in $item) { $rows += $entry }
        } else {
            $rows += $item
        }
    }

    foreach ($row in $rows) {
        if ($null -eq $row) { continue }
        $serviceName = [string]$row.Name
        $marker = switch ($serviceName) {
            'mcp' { 'app.mcp_server' }
            'worker' { 'app.worker' }
            'benchmark-worker' { 'app.benchmark_worker' }
            'api' { 'app.main:app' }
            default { '' }
        }
        if (-not $marker) {
            Write-Warning "Unknown process entry ignored: $serviceName"
            continue
        }

        $pidValue = $row.PID
        $targetPid = 0
        if ($null -eq $pidValue -or $pidValue -is [System.Array] -or
            -not [int]::TryParse([string]$pidValue, [ref]$targetPid) -or $targetPid -le 0) {
            Write-Warning "Invalid PID ignored for $serviceName"
            continue
        }

        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $targetPid" -ErrorAction SilentlyContinue
        if ($null -eq $proc) { continue }
        $commandLine = [string]$proc.CommandLine
        if ($commandLine.Contains($marker) -and
            $commandLine.IndexOf($expectedPython, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
            Write-Host ("Stopped {0} (PID {1})" -f $serviceName, $targetPid)
        } else {
            Write-Warning "PID $targetPid is not a matching NastaraOps process; it was left running."
        }
    }
    Remove-Item -LiteralPath $record -Force
} else {
    Write-Host 'No local Python process record found.'
}

& docker compose down
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Docker Compose reported an error. No volumes were removed by this script.'
} else {
    Write-Host 'NastaraOps stopped. PostgreSQL, Redis and Qdrant volumes are preserved.' -ForegroundColor Green
}

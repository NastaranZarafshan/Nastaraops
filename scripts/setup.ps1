# NastaraOps | Python 3.11 local environment setup
# Detects manually installed Python 3.11 even if the "py" launcher is not configured.
# Optional: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -PythonExe "C:\path\to\Python311\python.exe"
param(
    [string]$PythonExe = ''
)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$root = (Get-Location).Path
Write-Host 'NastaraOps | Finding Python 3.11...' -ForegroundColor Cyan

function Test-Python311([string]$executable, [string[]]$arguments = @()) {
    if (-not $executable) { return $null }
    try {
        # The script never accepts Python 3.13 for the project environment.
        $code = 'import sys; assert sys.version_info[:2] == (3, 11); print(sys.executable)'
        $result = & $executable @arguments -c $code 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $result) { return $null }
        $found = [string]($result | Select-Object -Last 1)
        $found = $found.Trim()
        if (Test-Path -LiteralPath $found -PathType Leaf) { return $found }
    } catch { }
    return $null
}

$systemPython = $null
if ($PythonExe) {
    # Explicit path has priority; reject it immediately if it is not Python 3.11.
    $systemPython = Test-Python311 $PythonExe
    if (-not $systemPython) { throw "The supplied -PythonExe is not a working Python 3.11 interpreter: $PythonExe" }
} else {
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($launcher) { $systemPython = Test-Python311 $launcher.Source @('-3.11') }

    if (-not $systemPython) {
        $commands = @(Get-Command -Name 'python3.11.exe','python.exe' -All -ErrorAction SilentlyContinue)
        foreach ($command in $commands) {
            $systemPython = Test-Python311 $command.Source
            if ($systemPython) { break }
        }
    }

    if (-not $systemPython) {
        $candidates = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
            (Join-Path $env:ProgramFiles 'Python311\python.exe'),
            'C:\Python311\python.exe'
        )
        $registryPaths = @(
            'HKCU:\Software\Python\PythonCore\3.11\InstallPath',
            'HKLM:\Software\Python\PythonCore\3.11\InstallPath',
            'HKLM:\Software\WOW6432Node\Python\PythonCore\3.11\InstallPath'
        )
        foreach ($key in $registryPaths) {
            if (Test-Path $key) {
                $registryKey = Get-Item $key
                $location = [string]$registryKey.GetValue('')
                $exeFromRegistry = [string]$registryKey.GetValue('ExecutablePath')
                if ($exeFromRegistry) { $candidates += $exeFromRegistry }
                if ($location) { $candidates += (Join-Path $location 'python.exe') }
            }
        }
        foreach ($candidate in $candidates) {
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
            $systemPython = Test-Python311 $candidate
            if ($systemPython) { break }
        }
    }
}

if (-not $systemPython) {
    throw 'Python 3.11 was not found automatically. Find your Python 3.11 python.exe, then rerun: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -PythonExe "C:\full\path\python.exe". Check available installations with: py -0p'
}
Write-Host "Using Python: $systemPython" -ForegroundColor Green
& $systemPython --version
if ($LASTEXITCODE -ne 0) { throw 'Selected Python could not run.' }

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $systemPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Check the selected Python installation.' }
}
& $venvPython -c 'import sys; print(sys.version); assert sys.version_info[:2] == (3, 11)'
if ($LASTEXITCODE -ne 0) {
    throw 'The existing .venv was created with a different Python version. Rename ONLY the .venv directory (for example to .venv-old), then rerun. Do not remove .env or Docker volumes.'
}

# Do not force a pip upgrade; it is not required and may time out on slower networks.
Write-Host 'Installing Python dependencies from requirements.txt...' -ForegroundColor Cyan
& $venvPython -m pip install --disable-pip-version-check --prefer-binary --timeout 180 --retries 6 -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw 'pip installation incomplete (network/PyPI or wheel compatibility). Existing files and credentials were preserved. Correct the connection, then rerun setup.'
}

if (-not (Test-Path '.env')) {
    function New-RandomHex([int]$count) {
        $bytes = New-Object byte[] $count
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    }
    $template = [IO.File]::ReadAllText((Join-Path $root '.env.example'))
    $content = $template.Replace('REPLACE_WITH_RANDOM_SECRET', (New-RandomHex 32))
    $content = $content.Replace('REPLACE_WITH_RANDOM_PASSWORD', (New-RandomHex 24))
    [IO.File]::WriteAllText((Join-Path $root '.env'), $content, [Text.UTF8Encoding]::new($false))
    Write-Host 'New .env created with unique API key and database credentials.' -ForegroundColor Green
} else {
    Write-Host '.env exists: keeping existing credentials.' -ForegroundColor Yellow
}
& $venvPython -c 'import fastapi, sqlalchemy, asyncpg, redis, langgraph, mcp, pydantic_settings'
if ($LASTEXITCODE -ne 0) { throw 'Library import verification failed.' }
Write-Host 'PASS: Core imports available' -ForegroundColor Green
Write-Host 'SETUP COMPLETE. Next: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1' -ForegroundColor Green

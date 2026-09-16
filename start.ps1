param([switch]$Restart, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
$launchLock = $null
try {
    Set-Location -LiteralPath $projectPath
    if (-not (Test-Path -LiteralPath '.env')) { throw 'Missing project/.env. Follow README configuration steps first.' }
    if (-not (Test-Path -LiteralPath 'D:\Anaconda\envs\hello_agents_py311\python.exe')) { throw 'Python environment missing. See README.' }
    if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) { throw 'Node.js is missing from PATH.' }
    if (-not (Test-Path -LiteralPath 'frontend\node_modules\vite\bin\vite.js')) { throw 'Frontend dependencies missing. Run npm ci in project/frontend first.' }
    if (-not (Test-Path -LiteralPath 'runtime\pgdata\PG_VERSION')) { throw 'Database is not initialized. Follow README first-time setup instructions.' }
    $lockPath = Join-Path $projectPath 'runtime\startup.lock'
    try { $launchLock = [System.IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another startup is in progress. Wait for it to finish.' }
    Write-Host '[1/3] Starting PostgreSQL...'
    & (Join-Path $projectPath 'start-postgres.ps1')
    Write-Host '[2/3] Starting embedding, API, worker and web services...'
    & (Join-Path $projectPath 'start-local.ps1') -Restart:$Restart
    Write-Host '[3/3] Waiting for services (up to 120 seconds)...'
    $deadline = (Get-Date).AddSeconds(120)
    $pending = @('http://127.0.0.1:8001/health','http://127.0.0.1:8000/api/ready','http://127.0.0.1:5173/')
    while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline) {
        $pending = @($pending | Where-Object {
            try {
                $response = Invoke-WebRequest -Uri $_ -UseBasicParsing -TimeoutSec 3
                if ($_ -like '*8001*') { -not (($response.Content | ConvertFrom-Json).ready) }
                else { $response.StatusCode -ne 200 }
            } catch { $true }
        })
        if ($pending.Count -gt 0) { Start-Sleep -Seconds 2 }
    }
    if ($pending.Count -gt 0) { throw ('Services not ready: ' + ($pending -join ', ') + '. See runtime/*-error.log.') }
    $records = @(Get-Content -LiteralPath 'runtime\local-processes.json' -Raw | ConvertFrom-Json)
    $workers = @($records | Where-Object {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue
        $p -and $p.CommandLine -match 'knowledge_agent\.worker'
    })
    if ($workers.Count -eq 0) { throw 'Worker exited. See runtime/worker-error.log.' }
    Write-Host 'Ready: http://127.0.0.1:5173/' -ForegroundColor Green
    Write-Host 'Admin login: admin / 123456 (unless you changed the password).'
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5173/' }
} catch {
    Write-Host ('Startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($launchLock) { $launchLock.Dispose() }
}

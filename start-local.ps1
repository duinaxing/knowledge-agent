param([switch]$SqliteDemo, [switch]$Restart)
$ErrorActionPreference = 'Stop'
# Fail before creating duplicate services when process inspection is unavailable.
$null = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
$projectRoot = $PSScriptRoot
$pythonPath = 'D:\Anaconda\envs\hello_agents_py311\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Activate the documented Python environment or update pythonPath.' }
$runtimePath = Join-Path $projectRoot 'runtime'
New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
$pidFile = Join-Path $runtimePath 'local-processes.json'
$previousProcesses = @()
if (Test-Path -LiteralPath $pidFile) { $previousProcesses = @(Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json) }
if ($Restart) {
    foreach ($record in $previousProcesses) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.Id)" -ErrorAction SilentlyContinue
        if ($process -and ($process.CommandLine -match 'knowledge_agent\.(api|worker|local_embeddings)' -or $process.CommandLine -like "*$projectRoot*\vite\bin\vite.js*")) {
            $terminated = Invoke-CimMethod -InputObject $process -MethodName Terminate -ErrorAction Stop
            if ($terminated.ReturnValue -ne 0) { throw 'Project process could not be stopped.' }
        }
    }
    $previousProcesses = @()
}
$env:PYTHONPATH = Join-Path $projectRoot 'backend\src'
if ($SqliteDemo) {
    $env:DATABASE_URL = 'sqlite:///' + (Join-Path $runtimePath 'demo.db').Replace('\','/')
    Write-Output 'SQLite demo mode: PostgreSQL locks, pgvector and cross-process checkpoints require separate acceptance.'
} else {
    $databaseLine = Get-Content -LiteralPath (Join-Path $projectRoot '.env') | Where-Object { $_ -like 'DATABASE_URL=*' } | Select-Object -First 1
    if ($databaseLine) { $env:DATABASE_URL = $databaseLine.Substring('DATABASE_URL='.Length) }
}
$processes = @()
if (-not (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)) {
    $processes += Start-Process -FilePath $pythonPath -ArgumentList '-m uvicorn knowledge_agent.local_embeddings:app --host 127.0.0.1 --port 8001' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'embedding.log') -RedirectStandardError (Join-Path $runtimePath 'embedding-error.log')
}
if ($SqliteDemo) { & $pythonPath -m knowledge_agent.bootstrap --sqlite-demo --seed }
if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    $processes += Start-Process -FilePath $pythonPath -ArgumentList '-m uvicorn knowledge_agent.api:app --host 127.0.0.1 --port 8000' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'api.log') -RedirectStandardError (Join-Path $runtimePath 'api-error.log')
}
# Recover a stopped worker even when the API is still running.
$workerAlive = @($previousProcesses | Where-Object {
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue
    $candidate -and $candidate.CommandLine -match 'knowledge_agent\.worker'
}).Count -gt 0
if (-not $workerAlive) {
    $processes += Start-Process -FilePath $pythonPath -ArgumentList '-m knowledge_agent.worker' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'worker.log') -RedirectStandardError (Join-Path $runtimePath 'worker-error.log')
}
if (-not (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)) {
    $vitePath = Join-Path $projectRoot 'frontend\node_modules\vite\bin\vite.js'
    $processes += Start-Process -FilePath 'node.exe' -ArgumentList @($vitePath, '--host', '127.0.0.1') -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'web.log') -RedirectStandardError (Join-Path $runtimePath 'web-error.log')
}
@($previousProcesses) + @($processes | Select-Object Id,ProcessName) | ConvertTo-Json | Set-Content -LiteralPath $pidFile
Write-Output 'Open http://127.0.0.1:5173. Logs are in project/runtime.'

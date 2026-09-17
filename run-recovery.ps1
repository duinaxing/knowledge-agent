param([ValidateSet('knowledge_loadtest_100','knowledge_quality_test')][string]$Source='knowledge_loadtest_100')
$ErrorActionPreference='Stop'
$previous=$env:PYTHONPATH
try {
    $env:PYTHONPATH=Join-Path $PSScriptRoot 'backend/src'
    Push-Location $PSScriptRoot
    try {
        & 'D:\Anaconda\envs\hello_agents_py311\python.exe' -m knowledge_quality.recovery --source $Source
        if ($LASTEXITCODE -ne 0) { throw 'Recovery drill failed; inspect runtime/recovery. Existing targets are never overwritten.' }
    } finally { Pop-Location }
} finally { $env:PYTHONPATH=$previous }

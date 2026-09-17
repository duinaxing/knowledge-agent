param([ValidateSet('all','matrix','full')][string]$Only='all',[string]$Experiment=('validation-'+(Get-Date -Format 'yyyyMMdd-HHmmss')))
$ErrorActionPreference='Stop'
$previous=$env:PYTHONPATH
try {
    $env:PYTHONPATH=Join-Path $PSScriptRoot 'backend/src'
    Push-Location $PSScriptRoot
    try {
        & 'D:\Anaconda\envs\hello_agents_py311\python.exe' -m knowledge_loadtest.campaign --only $Only --experiment $Experiment
        if ($LASTEXITCODE -ne 0) { throw 'Campaign failed; inspect runtime/loadtest/campaigns.' }
    } finally { Pop-Location }
} finally { $env:PYTHONPATH=$previous }

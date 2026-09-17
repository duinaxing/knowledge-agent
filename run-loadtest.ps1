param([ValidateSet('full','smoke','faults','burst','complex')][string]$Profile='full', [switch]$Real, [switch]$Cleanup, [ValidateRange(1,8)][int]$Workers=4, [switch]$LegacyAgent, [string]$Experiment='validation-v1')
$ErrorActionPreference='Stop'
$pythonPath='D:\Anaconda\envs\hello_agents_py311\python.exe'
$oldPythonPath=$env:PYTHONPATH
Push-Location $PSScriptRoot
try {
    $env:PYTHONPATH=Join-Path $PSScriptRoot 'backend\src'
    $arguments=@('-m','knowledge_loadtest.launch','--profile',$Profile,'--workers',[string]$Workers,'--experiment',$Experiment)
    if ($LegacyAgent) { $arguments+='--legacy-agent' }
    if ($Real) { $arguments+='--real' }
    if ($Cleanup) { $arguments+='--cleanup' }
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Load test failed. Inspect project/runtime/loadtest/results logs.' }
} finally { Pop-Location; $env:PYTHONPATH=$oldPythonPath }

param([ValidateSet('full','smoke','faults')][string]$Profile='full', [switch]$Real, [switch]$Cleanup, [ValidateRange(1,8)][int]$Workers=4, [switch]$LegacyAgent)
$ErrorActionPreference='Stop'
$pythonPath='D:\Anaconda\envs\hello_agents_py311\python.exe'
$oldPythonPath=$env:PYTHONPATH
try {
    $env:PYTHONPATH=Join-Path $PSScriptRoot 'backend\src'
    $arguments=@('-m','knowledge_loadtest.launch','--profile',$Profile,'--workers',[string]$Workers)
    if ($LegacyAgent) { $arguments+='--legacy-agent' }
    if ($Real) { $arguments+='--real' }
    if ($Cleanup) { $arguments+='--cleanup' }
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Load test failed. Inspect project/runtime/loadtest/results logs.' }
} finally { $env:PYTHONPATH=$oldPythonPath }

param([ValidateSet('dev','heldout')][string]$Split='dev',[switch]$Real,[string]$Experiment='quality-v1')
$ErrorActionPreference='Stop'
$previousPath=$env:PYTHONPATH
$previousSplit=$env:QUALITY_SPLIT
Push-Location $PSScriptRoot
try {
    $env:PYTHONPATH=Join-Path $PSScriptRoot 'backend/src'
    $env:QUALITY_SPLIT=$Split
    $arguments=@('-m','knowledge_quality.launch','--workers','8','--experiment',$Experiment)
    if ($Real) { $arguments+='--real' }
    & 'D:\Anaconda\envs\hello_agents_py311\python.exe' @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Quality evaluation failed; inspect runtime/quality.' }
} finally { Pop-Location; $env:PYTHONPATH=$previousPath; $env:QUALITY_SPLIT=$previousSplit }

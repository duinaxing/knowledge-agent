$ErrorActionPreference = 'Stop'
$pgRuntime = Join-Path $PSScriptRoot 'runtime\postgres\Library'
$pgData = Join-Path $PSScriptRoot 'runtime\pgdata'
$pgLog = Join-Path $PSScriptRoot 'runtime\postgres.log'
$pgPasswordFile = Join-Path $PSScriptRoot 'runtime\postgres-password.txt'
if (-not (Test-Path -LiteralPath (Join-Path $pgRuntime 'bin\pg_ctl.exe'))) {
    throw 'Install the documented project PostgreSQL runtime first.'
}
$previousPath = $env:PATH
$previousPassword = $env:PGPASSWORD
try {
    $env:PATH = (Join-Path $pgRuntime 'bin') + ';' + $previousPath
    if (-not (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION'))) {
        Set-Content -LiteralPath $pgPasswordFile -Value 'knowledge' -Encoding ascii
        & (Join-Path $pgRuntime 'bin\initdb.exe') -D $pgData -U knowledge --pwfile=$pgPasswordFile --auth=scram-sha-256 --encoding=UTF8 --locale=C
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL initialization failed.' }
        Add-Content -LiteralPath (Join-Path $pgData 'postgresql.conf') -Value "`nlisten_addresses = '127.0.0.1'`nport = 5432"
    }
    & (Join-Path $pgRuntime 'bin\pg_ctl.exe') -D $pgData status
    if ($LASTEXITCODE -ne 0) {
        & (Join-Path $pgRuntime 'bin\pg_ctl.exe') -D $pgData -l $pgLog -w start
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed. See runtime/postgres.log.' }
    }
    $env:PGPASSWORD = 'knowledge'
    $databaseExists = & (Join-Path $pgRuntime 'bin\psql.exe') -h 127.0.0.1 -U knowledge -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='knowledge'"
    if ($databaseExists -ne '1') {
        & (Join-Path $pgRuntime 'bin\createdb.exe') -h 127.0.0.1 -U knowledge knowledge
        if ($LASTEXITCODE -ne 0) { throw 'Database creation failed.' }
    }
    Write-Output 'Project PostgreSQL is ready on 127.0.0.1:5432.'
} finally {
    $env:PATH = $previousPath
    $env:PGPASSWORD = $previousPassword
}

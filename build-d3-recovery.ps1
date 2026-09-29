$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = @(& $vswhere -all -prerelease -products '*' -property installationPath) |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat') } |
    Select-Object -First 1
if (-not $vs) { throw 'No Visual Studio Build Tools installation was found.' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$output = Join-Path $project 'build\recover-d3'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$source = Join-Path $project 'src\recover_database_d3.cpp'
$exe = Join-Path $output 'recover_database_d3.exe'
$command = '"' + $devcmd + '" -no_logo -arch=x64 -host_arch=x64 && ' +
    'cl /nologo /O2 /EHsc /std:c++17 /W4 /Fe:"' + $exe + '" "' + $source + '"'
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }
Write-Output "Built $exe"

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = @(& $vswhere -all -prerelease -products '*' -property installationPath) |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat') } |
    Select-Object -First 1
if (-not $vs) { throw 'Visual Studio Build Tools fehlen' }
$out = Join-Path $project 'build\display-policy'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$source = Join-Path $project 'tests\display_policy_test.cpp'
$artifact = Join-Path $out 'display-policy-test.exe'
$obj = Join-Path $out 'display-policy-test.obj'
$command = '"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && cl /nologo /W4 /O1 /Fo"' +
    $obj + '" /Fe"' + $artifact + '" "' + $source + '" && "' + $artifact + '"'
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "Display policy checks failed: $LASTEXITCODE" }
Write-Output 'Eight native display placement checks passed; no GUI or QEMU started.'

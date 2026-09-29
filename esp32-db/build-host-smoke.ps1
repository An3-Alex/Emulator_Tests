$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $project
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -all -prerelease -products '*' -property installationPath |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat') } |
    Select-Object -First 1
if (-not $vs) { throw 'Visual Studio C Build Tools not found' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$out = Join-Path $project 'build'
$generated = Join-Path $project 'generated'
New-Item -ItemType Directory -Force -Path $out,$generated | Out-Null
$musashi = Join-Path $root 'third_party\Musashi'
$generator = Join-Path $out 'm68kmake.exe'
$compileGenerator = 'cl /nologo /TC /O2 /Fe:"' + $generator + '" "' +
    (Join-Path $musashi 'm68kmake.c') + '"'
$command = '"' + $devcmd + '" -no_logo -arch=x64 -host_arch=x64 && ' +
    $compileGenerator
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "Musashi generator build failed: $LASTEXITCODE" }
& $generator $generated (Join-Path $musashi 'm68k_in.c')
if ($LASTEXITCODE -ne 0) { throw "Musashi code generation failed: $LASTEXITCODE" }
python (Join-Path $project 'place_musashi_tables.py') (Join-Path $generated 'm68kops.c')
if ($LASTEXITCODE -ne 0) { throw "Musashi PSRAM placement failed: $LASTEXITCODE" }

$sources = @(
    (Join-Path $project 'tests\smoke.c'),
    (Join-Path $project 'core\db_machine.c'),
    (Join-Path $project 'core\db_rtc.c'),
    (Join-Path $project 'core\db_cpu.c'),
    (Join-Path $project 'core\db_clock.c'),
    (Join-Path $musashi 'm68kcpu.c'),
    (Join-Path $generated 'm68kops.c'),
    (Join-Path $musashi 'softfloat\softfloat.c')
)
$exe = Join-Path $out 'db-cpu-smoke.exe'
$compile = 'cl /nologo /TC /O2 /W3 /DM68K_INSTRUCTION_HOOK=1 /DM68K_EMULATE_030=0 ' +
    '/DM68K_EMULATE_040=0 /DM68K_EMULATE_PMMU=0 ' +
    '/I"' + (Join-Path $project 'core') + '" ' +
    '/I"' + $musashi + '" /I"' + $generated + '" ' +
    '/Fe:"' + $exe + '" ' + (($sources | ForEach-Object { '"' + $_ + '"' }) -join ' ')
$command = '"' + $devcmd + '" -no_logo -arch=x64 -host_arch=x64 && ' + $compile
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "CPU smoke build failed: $LASTEXITCODE" }
& $exe
if ($LASTEXITCODE -ne 0) { throw "CPU smoke failed: $LASTEXITCODE" }

$ownerProbe = Join-Path $out 'db-owner-probe.exe'
$ownerSources = @((Join-Path $project 'tests\owner_probe.c')) + $sources[1..($sources.Count - 1)]
$compileOwner = 'cl /nologo /TC /O2 /w /DM68K_INSTRUCTION_HOOK=1 /DM68K_EMULATE_030=0 ' +
    '/DM68K_EMULATE_040=0 /DM68K_EMULATE_PMMU=0 ' +
    '/I"' + (Join-Path $project 'core') + '" ' +
    '/I"' + $musashi + '" /I"' + $generated + '" ' +
    '/Fe:"' + $ownerProbe + '" ' +
    (($ownerSources | ForEach-Object { '"' + $_ + '"' }) -join ' ')
$command = '"' + $devcmd + '" -no_logo -arch=x64 -host_arch=x64 && ' + $compileOwner
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "Owner probe build failed: $LASTEXITCODE" }
Write-Host "Built $ownerProbe"

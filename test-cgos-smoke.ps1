$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsCandidates = @(& $vswhere -all -prerelease -products '*' -property installationPath)
$vs = $vsCandidates | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat') } | Select-Object -First 1
if (-not $vs) { throw 'No Visual Studio Build Tools installation was found.' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$out = Join-Path $project 'build\tests'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$source = Join-Path $project 'tests\cgos_smoke.c'
$object = Join-Path $out 'cgos_smoke.obj'
$exe = Join-Path $out 'cgos_smoke.exe'
$import = Join-Path $project 'build\Cgos.lib'
$include = Join-Path $project 'include'
$compile = 'cl /nologo /c /Brepro /TC /O1 /GS- /Zl /W4 /D_WIN32_WINNT=0x0501 /DWINVER=0x0501 ' +
  '/I"' + $include + '" /Fo"' + $object + '" "' + $source + '"'
$link = 'link /nologo /Brepro /MACHINE:X86 /SUBSYSTEM:CONSOLE,5.01 /OSVERSION:5.1 /NODEFAULTLIB ' +
  '/ENTRY:mainCRTStartup /OUT:"' + $exe + '" "' + $object + '" "' + $import + '" kernel32.lib'
& $env:ComSpec /d /s /c ('"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && ' + $compile + ' && ' + $link)
if ($LASTEXITCODE -ne 0) { throw "Smoke-test build failed: $LASTEXITCODE" }
Copy-Item -Force (Join-Path $project 'build\Cgos.dll') (Join-Path $out 'Cgos.dll')
& $exe
if ($LASTEXITCODE -ne 0) { throw "CGOS smoke test failed: $LASTEXITCODE" }
Write-Host 'CGOS smoke test passed.'

$ErrorActionPreference='Stop'
$vswhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs=@(& $vswhere -all -prerelease -products '*' -property installationPath) | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat') } | Select-Object -First 1
if(-not $vs){throw 'Visual Studio Build Tools not found'}
$devcmd=Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$out=Join-Path $PSScriptRoot ('build\gpu-mapper-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out | Out-Null
$source=Join-Path $PSScriptRoot 'tests\gpu_mapper_harness.cpp'
$obj=Join-Path $out 'harness.obj'
$exe=Join-Path $out 'harness.exe'
& $env:ComSpec /d /s /c ('"'+$devcmd+'" -no_logo -arch=x86 -host_arch=x64 && cl /nologo /c /O1 /GS- /GR- /EHs-c- /Zl /W4 /Fo"'+$obj+'" "'+$source+'" && link /nologo /MACHINE:X86 /SUBSYSTEM:CONSOLE,5.01 /NODEFAULTLIB /ENTRY:mainCRTStartup /OUT:"'+$exe+'" "'+$obj+'" kernel32.lib user32.lib uuid.lib dxguid.lib')
if($LASTEXITCODE -ne 0){throw 'Mapper harness build failed'}
Push-Location $out
try { & $exe; if($LASTEXITCODE -ne 0){throw "Mapper harness failed: $LASTEXITCODE"} }
finally { Pop-Location }

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = @(& $vswhere -all -prerelease -products '*' -property installationPath) | Where-Object {
    Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat')
} | Select-Object -First 1
if (-not $vs) { throw 'Visual Studio Build Tools not found' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$out = Join-Path $project ('build\d3d9-backend-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out | Out-Null
$src = Join-Path $project 'tests\d3d9_backend_harness.cpp'
$obj = Join-Path $out 'harness.obj'
$exe = Join-Path $out 'harness.exe'
$command = '"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && ' +
    'cl /nologo /c /O1 /GS- /GR- /EHs-c- /Zl /W4 /D_WIN32_WINNT=0x0501 /DWINVER=0x0501 /Fo"' + $obj + '" "' + $src + '" && ' +
    'link /nologo /MACHINE:X86 /SUBSYSTEM:CONSOLE,5.01 /NODEFAULTLIB /ENTRY:mainCRTStartup /OUT:"' + $exe + '" "' + $obj + '" kernel32.lib user32.lib advapi32.lib uuid.lib dxguid.lib'
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw 'D3D9 backend harness build failed' }
Push-Location $out
try { & $exe; if ($LASTEXITCODE -ne 0) { throw "D3D9 backend harness failed: $LASTEXITCODE" } }
finally { Pop-Location }

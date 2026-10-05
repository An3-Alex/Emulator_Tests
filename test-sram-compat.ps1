$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = @(& $vswhere -all -prerelease -products '*' -property installationPath) |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat') } | Select-Object -First 1
if (-not $vs) { throw 'Visual Studio Build Tools missing' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$out = Join-Path $project ('build\sram-harness-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out | Out-Null
$source = Join-Path $project 'tests\sram_compat_harness.c'
$exe = Join-Path $out 'sram-harness.exe'
$obj = Join-Path $out 'sram-harness.obj'
$command = '"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && cl /nologo /TC /O1 /GS- /Zl /D_WIN32_WINNT=0x0501 /DWINVER=0x0501 /Fo"' + $obj + '" /Fe"' + $exe + '" "' + $source + '" /link /NODEFAULTLIB /ENTRY:mainCRTStartup /SUBSYSTEM:CONSOLE,5.01 kernel32.lib'
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw 'SRAM harness build failed' }
Push-Location $out
try {
    & $exe
    if ($LASTEXITCODE -ne 0) { throw "SRAM harness failed: $LASTEXITCODE" }
    Get-Content -LiteralPath (Join-Path $out 'sram-harness.log') -Tail 1
} finally { Pop-Location }

$loaderDll = Join-Path $project 'tests\sram_compat_loader_dll.c'
$loaderExe = Join-Path $project 'tests\service_sram_loader_harness.c'
$def = Join-Path $project 'src\FBWFLIB.def'
Push-Location $out
try {
    $command = '"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && cl /nologo /TC /O1 /GS- /Zl /D_WIN32_WINNT=0x0501 /DWINVER=0x0501 /LD "' + $loaderDll + '" /link /NODEFAULTLIB /ENTRY:DllMain /DEF:"' + $def + '" /OUT:FBWFLIB.dll /IMPLIB:FBWFLIB.lib kernel32.lib && cl /nologo /TC /O1 /GS- /Zl /D_WIN32_WINNT=0x0501 /DWINVER=0x0501 /Fe:GGSG_Servic.exe "' + $loaderExe + '" /link /NODEFAULTLIB /ENTRY:mainCRTStartup /SUBSYSTEM:CONSOLE,5.01 kernel32.lib setupapi.lib FBWFLIB.lib'
    & $env:ComSpec /d /s /c $command
    if ($LASTEXITCODE -ne 0) { throw 'Service loader harness build failed' }
    # Both backing storage and log are confined to this disposable directory.
    & (Join-Path $out 'GGSG_Servic.exe')
    if ($LASTEXITCODE -ne 0) { throw "Service loader harness failed: $LASTEXITCODE" }
    Write-Output 'SERVICE_LOADER_PASS real PE import, DLL initialization, ANSI IAT, shared SRAM'
} finally { Pop-Location }

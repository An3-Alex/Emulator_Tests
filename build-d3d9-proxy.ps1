param([switch]$Qemu3dfx)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsCandidates = @(& $vswhere -all -prerelease -products '*' -property installationPath)
$vs = $vsCandidates | Where-Object {
    Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat')
} | Select-Object -First 1
if (-not $vs) { throw 'No Visual Studio Build Tools installation was found.' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$out = Join-Path $project $(if ($Qemu3dfx) { 'build\d3d9-qemu3dfx' } else { 'build\d3d9-proxy' })
New-Item -ItemType Directory -Force -Path $out | Out-Null
$src = Join-Path $project 'src\d3d9_proxy.cpp'
$def = Join-Path $project 'src\d3d9_proxy.def'
$obj = Join-Path $out 'd3d9_proxy.obj'
$dll = Join-Path $out 'd3d9.dll'
$map = Join-Path $out 'd3d9.map'
$compile = 'cl /nologo /c /Brepro /O1 /GS- /GR- /EHs-c- /Zl /W4 ' +
           '/D_WIN32_WINNT=0x0501 /DWINVER=0x0501 ' +
           '/Fo"' + $obj + '" "' + $src + '"'
if ($Qemu3dfx) { $compile += ' /DM90_QEMU3DFX=1' }
$link = 'link /nologo /Brepro /DLL /MACHINE:X86 /SUBSYSTEM:WINDOWS,5.01 /OSVERSION:5.1 ' +
        '/NODEFAULTLIB /ENTRY:DllMain /OUT:"' + $dll + '" ' +
        '/MAP:"' + $map + '" /DEF:"' + $def + '" "' + $obj + '" kernel32.lib user32.lib uuid.lib dxguid.lib'
$command = '"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && ' +
           $compile + ' && ' + $link
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }
Write-Host "Built $dll"

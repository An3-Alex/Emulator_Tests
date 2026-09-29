param(
    [ValidateSet('phase1', 'phase2')]
    [string]$Mode = 'phase1'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) {
    throw 'Visual Studio Build Tools (vswhere.exe) were not found.'
}

$vsCandidates = @(& $vswhere -all -prerelease -products '*' -property installationPath)
$vs = $vsCandidates | Where-Object {
    Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat')
} | Select-Object -First 1
if (-not $vs) { throw 'No Visual Studio Build Tools installation was found.' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
if (-not (Test-Path -LiteralPath $devcmd)) { throw "VsDevCmd.bat not found: $devcmd" }

$out = Join-Path $project 'build'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$src = Join-Path $project 'src\cgos_shim.c'
$def = Join-Path $project 'src\Cgos.def'
$inc = Join-Path $project 'include'
$obj = Join-Path $out 'cgos_shim.obj'
$dll = Join-Path $out 'Cgos.dll'
$map = Join-Path $out 'Cgos.map'

$emulate = if ($Mode -eq 'phase2') { '1' } else { '0' }
$compile = 'cl /nologo /c /Brepro /TC /O1 /GS- /Zl /W4 /D_WIN32_WINNT=0x0501 /DWINVER=0x0501 ' +
           '/DCGOS_EMULATE_BOARD=' + $emulate + ' ' +
           '/I"' + $inc + '" /Fo"' + $obj + '" "' + $src + '"'
$link = 'link /nologo /Brepro /DLL /MACHINE:X86 /SUBSYSTEM:WINDOWS,5.01 /OSVERSION:5.1 ' +
        '/NODEFAULTLIB /ENTRY:DllMain /DEF:"' + $def + '" /OUT:"' + $dll + '" ' +
        '/MAP:"' + $map + '" "' + $obj + '" kernel32.lib'
$cmd = '"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && ' + $compile + ' && ' + $link

& $env:ComSpec /d /s /c $cmd
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }
Write-Host "Built $dll ($Mode)"

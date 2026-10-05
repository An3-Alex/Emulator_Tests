$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsCandidates = @(& $vswhere -all -prerelease -products '*' -property installationPath)
$vs = $vsCandidates | Where-Object {
    Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat')
} | Select-Object -First 1
if (-not $vs) { throw 'No Visual Studio Build Tools installation was found.' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$out = Join-Path $project 'build\sram-compat'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$src = Join-Path $project 'src\sram_compat.c'
$def = Join-Path $project 'src\FBWFLIB.def'
$obj = Join-Path $out 'sram_compat.obj'
$dll = Join-Path $out 'FBWFLIB.dll'
$map = Join-Path $out 'FBWFLIB.map'
$compile = 'cl /nologo /c /Brepro /TC /O1 /GS- /Zl /W4 /D_WIN32_WINNT=0x0501 /DWINVER=0x0501 ' +
           '/Fo"' + $obj + '" "' + $src + '"'
$link = 'link /nologo /Brepro /DLL /MACHINE:X86 /SUBSYSTEM:WINDOWS,5.01 /OSVERSION:5.1 ' +
        '/NODEFAULTLIB /ENTRY:DllMain /DEF:"' + $def + '" /OUT:"' + $dll + '" ' +
        '/MAP:"' + $map + '" "' + $obj + '" kernel32.lib'
$command = '"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && ' +
           $compile + ' && ' + $link
& $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }
Write-Host "Built $dll"

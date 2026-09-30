$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsCandidates = @(& $vswhere -all -prerelease -products '*' -property installationPath)
$vs = $vsCandidates | Where-Object {
    Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat')
} | Select-Object -First 1
if (-not $vs) { throw 'No Visual Studio Build Tools installation was found.' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$out = Join-Path $project 'build'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$src = Join-Path $project 'src\display_config.c'
$obj = Join-Path $out 'display_verify.obj'
$exe = Join-Path $out 'display-verify.exe'
$map = Join-Path $out 'display-verify.map'
$compile = 'cl /nologo /c /TC /O1 /GS- /Zl /W4 /D_WIN32_WINNT=0x0501 ' +
           '/DWINVER=0x0501 /DDISPLAY_VERIFY=1 /Fo"' + $obj + '" "' + $src + '"'
$link = 'link /nologo /MACHINE:X86 /SUBSYSTEM:CONSOLE,5.01 /OSVERSION:5.1 ' +
        '/NODEFAULTLIB /ENTRY:mainCRTStartup /OUT:"' + $exe + '" ' +
        '/MAP:"' + $map + '" "' + $obj + '" kernel32.lib'
$cmd = '"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && ' +
       $compile + ' && ' + $link
& $env:ComSpec /d /s /c $cmd
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }
Write-Host "Built $exe"

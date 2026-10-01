param([string[]]$Components = @('audio-installer', 'audio-verify'))
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = @(& $vswhere -all -prerelease -products '*' -property installationPath) | Where-Object {
    Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat')
} | Select-Object -First 1
if (-not $vs) { throw 'Visual Studio Build Tools fehlen.' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
New-Item -ItemType Directory -Force -Path (Join-Path $project 'build') | Out-Null
foreach ($name in $Components) {
    if ($name -notin @('audio-installer', 'audio-verify')) { throw 'Unknown component' }
    $source = Join-Path $project ('src\' + $name.Replace('-', '_') + '.c')
    $obj = Join-Path $project "build\$name.obj"
    $exe = Join-Path $project "build\$name.exe"
    $compile = "cl /nologo /c /TC /O1 /GS- /Zl /W4 /D_WIN32_WINNT=0x0501 /DWINVER=0x0501 /Fo`"$obj`" `"$source`""
    $link = "link /nologo /MACHINE:X86 /SUBSYSTEM:CONSOLE,5.01 /OSVERSION:5.1 /NODEFAULTLIB /ENTRY:mainCRTStartup /OUT:`"$exe`" `"$obj`" kernel32.lib user32.lib winmm.lib setupapi.lib cfgmgr32.lib advapi32.lib"
    & $env:ComSpec /d /s /c ('"' + $devcmd + '" -no_logo -arch=x86 -host_arch=x64 && ' + $compile + ' && ' + $link)
    if ($LASTEXITCODE -ne 0) { throw "Build fehlgeschlagen: $name" }
    Get-FileHash -LiteralPath $exe -Algorithm SHA256 | Select-Object Hash,Path
}

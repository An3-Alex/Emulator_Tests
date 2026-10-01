param(
    [Parameter(Mandatory=$true)][string]$OriginalIrrKlang,
    [Parameter(Mandatory=$true)][string]$SoundFile
)
$ErrorActionPreference = 'Stop'
$taskProject = $PSScriptRoot
$taskOriginal = (Resolve-Path -LiteralPath $OriginalIrrKlang).Path
$taskSound = (Resolve-Path -LiteralPath $SoundFile).Path
if ((Get-FileHash -LiteralPath $taskOriginal -Algorithm SHA256).Hash -ne
    'AB0BFF115CF3F55A608A7059AE3FD5FDC73A5F4EE814DB4AA4B6C7E44CCE8297') {
    throw 'Unsupported original irrKlang version; expected owner version 1.1.3.'
}
$taskVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$taskVs = @(& $taskVswhere -all -prerelease -products '*' -property installationPath) |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'Common7\Tools\VsDevCmd.bat') } | Select-Object -First 1
if (-not $taskVs) { throw 'Visual Studio Build Tools missing.' }
$taskDev = Join-Path $taskVs 'Common7\Tools\VsDevCmd.bat'
$taskOutput = Join-Path $taskProject 'build\audio-probe'
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$taskObj = Join-Path $taskOutput 'proxy.obj'
$taskDll = Join-Path $taskOutput 'proxy.dll'
$taskExe = Join-Path $taskOutput 'audio-probe.exe'
$taskProbeObj = Join-Path $taskOutput 'probe.obj'
$taskSource = Join-Path $taskProject 'src\irrklang_proxy.c'
$taskProbe = Join-Path $taskProject 'tests\irrklang_audio_probe.cpp'
# Same source as production, but only this build permits local original/log
# paths. Production never reads the test environment variables.
$taskCommand = '"' + $taskDev + '" -no_logo -arch=x86 -host_arch=x64 && cl /nologo /c /TP /O1 /GS- /Zl /W4 /DIRRKLANG_OFFLINE_PROBE /Fo"' + $taskObj + '" "' + $taskSource + '" && link /nologo /DLL /MACHINE:X86 /NODEFAULTLIB /ENTRY:DllMain /OUT:"' + $taskDll + '" "' + $taskObj + '" kernel32.lib winmm.lib && cl /nologo /MT /W4 /O1 /Fo"' + $taskProbeObj + '" /Fe"' + $taskExe + '" "' + $taskProbe + '"'
& $env:ComSpec /d /s /c $taskCommand
if ($LASTEXITCODE -ne 0) { throw 'Native audio probe build failed.' }
$taskPreviousDll = $env:M90_TEST_IRRKLANG_DLL
$taskPreviousLog = $env:M90_TEST_AUDIO_LOG
try {
    $env:M90_TEST_IRRKLANG_DLL = $taskOriginal
    $env:M90_TEST_AUDIO_LOG = Join-Path $taskOutput 'proxy.log'
    & $taskExe $taskDll $taskSound
    if ($LASTEXITCODE -ne 0) { throw "Audio probe failed: $LASTEXITCODE" }
    Write-Output 'AUDIO_PROBE_OK real sound sources and objects; output muted'
} finally {
    $env:M90_TEST_IRRKLANG_DLL = $taskPreviousDll
    $env:M90_TEST_AUDIO_LOG = $taskPreviousLog
}

param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $project
try {
    & $Python -c 'import PyInstaller'
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller fehlt. In der Build-Umgebung installieren: python -m pip install pyinstaller'
    }
    $arguments = @(
        '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
        '--name', 'M90-Emulator',
        '--distpath', 'build\dist',
        '--workpath', 'build\pyinstaller',
        '--specpath', 'build\pyinstaller'
    )
    foreach ($name in @(
        'program-and-start-emulator.ps1', 'start-real-database.ps1',
        'test-swiftshader.ps1'
    )) {
        $source = Join-Path $project $name
        $arguments += @('--add-data', "${source}:.")
    }
    foreach ($file in Get-ChildItem -LiteralPath 'scripts' -Filter '*.py' -File) {
        $arguments += @('--add-data', "$($file.FullName):scripts")
    }
    foreach ($name in @(
        'prepare_image_stage.sh', 'stage_display_verify.sh', 'retry_qxl_install.sh',
        'finalize_image_stage.sh', 'check_image_stage.sh', 'update_runtime_graphics.sh',
        'stage_audio_image.sh', 'export_audio_diagnostics.sh', 'image_partition.sh'
    )) {
        $source = Join-Path $project "scripts\$name"
        $arguments += @('--add-data', "${source}:scripts")
    }
    foreach ($name in @(
        'build\Cgos.dll', 'build\display-bootstrap.exe', 'build\qxl-installer.exe',
        'build\display-verify.exe',
        'build\audio-software.exe', 'build\audio-installer.exe', 'build\audio-verify.exe',
        'build\d3d9-proxy\d3d9.dll', 'build\sram-compat\FBWFLIB.dll',
        'build\irrklang-proxy\irrKlang.dll'
    )) {
        $source = Join-Path $project $name
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Eigenkomponente fehlt. Zuerst bauen: $source"
        }
        $destination = (Split-Path -Parent $name).Replace('\', '/')
        $arguments += @('--add-data', "${source}:$destination")
    }
    $arguments += 'scripts/emulator_launcher.py'
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen: $LASTEXITCODE" }
    $artifact = Join-Path $project 'build\dist\M90-Emulator.exe'
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "EXE wurde nicht erzeugt: $artifact"
    }
    # The real AppData runtime folder contains a space ("M90 Emulator").
    # Exercise that path shape in every packaging self-test.
    $testRuntime = Join-Path $project 'build\bundle selftest'
    $testReport = Join-Path $testRuntime 'report.json'
    if (Test-Path -LiteralPath $testReport) {
        Remove-Item -LiteralPath $testReport
    }
    $previousRuntime = $env:M90_RUNTIME_ROOT
    try {
        $env:M90_RUNTIME_ROOT = $testRuntime
        $verification = Start-Process -FilePath $artifact -ArgumentList @(
            '--verify-bundle', ('"{0}"' -f $testReport)
        ) -WindowStyle Hidden -PassThru -Wait
    } finally {
        if ($null -eq $previousRuntime) {
            Remove-Item Env:M90_RUNTIME_ROOT -ErrorAction SilentlyContinue
        } else {
            $env:M90_RUNTIME_ROOT = $previousRuntime
        }
    }
    if ($verification.ExitCode -ne 0 -or
        -not (Test-Path -LiteralPath $testReport -PathType Leaf)) {
        throw "EXE-Selbstprüfung fehlgeschlagen (Code $($verification.ExitCode))"
    }
    Write-Output 'BUNDLE_VERIFIED scripts, start files and component hashes'
    Get-Item -LiteralPath $artifact | Select-Object FullName,Length
    Get-FileHash -LiteralPath $artifact -Algorithm SHA256 | Select-Object Hash
} finally {
    Pop-Location
}

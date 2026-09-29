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
    $arguments += 'scripts/emulator_launcher.py'
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen: $LASTEXITCODE" }
    $artifact = Join-Path $project 'build\dist\M90-Emulator.exe'
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "EXE wurde nicht erzeugt: $artifact"
    }
    Get-Item -LiteralPath $artifact | Select-Object FullName,Length
    Get-FileHash -LiteralPath $artifact -Algorithm SHA256 | Select-Object Hash
} finally {
    Pop-Location
}

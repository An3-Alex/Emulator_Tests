param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('install', 'check', 'build')]
    [string]$Action,
    [string]$SdkConfig,
    [string]$BuildDirectory
)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$idfRepo = Join-Path $projectDir '.tools\idf\v5.5.1\esp-idf'
$env:IDF_TOOLS_PATH = Join-Path $projectDir '.tools\esp-idf-tools'
$python313 = (& py -3.13 -c 'import sys; print(sys.executable)')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python313)) {
    throw 'Für ESP-IDF wird eine Systeminstallation von Python 3.13 benötigt.'
}
$env:PATH = "$(Split-Path -Parent $python313);$env:PATH"

if ($Action -eq 'install') {
    if (-not (Test-Path -LiteralPath (Join-Path $idfRepo 'tools\idf.py'))) {
        if (Test-Path -LiteralPath $idfRepo) {
            throw 'Unvollständiger ESP-IDF-Quellordner; bitte den Fehler prüfen, bevor er ersetzt wird.'
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $idfRepo) | Out-Null
        & git -c core.longpaths=true clone --branch v5.5.1 --depth 1 --recurse-submodules `
            https://github.com/espressif/esp-idf.git $idfRepo
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    & git -C $idfRepo -c core.longpaths=true submodule update --init --recursive
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & (Join-Path $idfRepo 'install.ps1') esp32s3
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    exit 0
}

if (-not (Test-Path -LiteralPath (Join-Path $idfRepo 'tools\idf.py'))) {
    throw 'ESP-IDF 5.5.1 fehlt. Bitte zuerst Toolchain einrichten.'
}

. (Join-Path $idfRepo 'export.ps1')
if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    throw 'ESP-IDF konnte nicht aktiviert werden. Toolchain einrichten erneut ausführen.'
}
if ($Action -eq 'check') {
    & idf.py --version
    exit $LASTEXITCODE
}

$sdkconfig = if ($SdkConfig) { $SdkConfig } else { Join-Path $projectDir '.generated\sdkconfig.gui' }
if (-not (Test-Path -LiteralPath $sdkconfig)) {
    throw "ESP-IDF-Konfiguration fehlt: $sdkconfig"
}
$buildPath = if ($BuildDirectory) { $BuildDirectory } else { Join-Path $projectDir 'build-idf' }
Push-Location -LiteralPath $projectDir
try {
    & idf.py -D "SDKCONFIG=$sdkconfig" -B $buildPath build
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

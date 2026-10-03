param(
    [Parameter(Mandatory)][string]$SourceImage,
    [Parameter(Mandatory)][string]$Image,
    [string]$Bundle = (Join-Path $PSScriptRoot 'build\qemu3dfx-runtime'),
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
if (Get-Process qemu-system* -ErrorAction SilentlyContinue) { throw 'QEMU muss vor der Offline-Vorbereitung beendet sein.' }
$sourcePath = (Resolve-Path -LiteralPath $SourceImage).Path
$imagePath = (Resolve-Path -LiteralPath $Image).Path
$bundlePath = (Resolve-Path -LiteralPath $Bundle).Path
if ($sourcePath -eq $imagePath) { throw 'Eine getrennte, bereits vorbereitete Arbeitskopie für QEMU-3dfx wählen.' }
& $Python (Join-Path $PSScriptRoot 'scripts\qemu3dfx_package.py') validate $bundlePath
if ($LASTEXITCODE -ne 0) { throw 'Grafikpaket unvollständig oder verändert.' }
function Convert-ToWslPath([string]$Path) {
    $result = & wsl.exe --exec wslpath -a -u $Path
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($result)) { throw 'WSL-Pfad konnte nicht bestimmt werden.' }
    return ($result | Select-Object -Last 1).Trim()
}
$script = Convert-ToWslPath (Join-Path $PSScriptRoot 'scripts\stage_qemu3dfx.sh')
$sourceWsl = Convert-ToWslPath $sourcePath
$imageWsl = Convert-ToWslPath $imagePath
$bundleWsl = Convert-ToWslPath $bundlePath
& wsl.exe --user root --exec bash $script $sourceWsl $imageWsl $bundleWsl $imagePath
if ($LASTEXITCODE -ne 0) { throw 'QEMU-3dfx-Vorbereitung fehlgeschlagen; keine VM gestartet.' }
& $Python (Join-Path $PSScriptRoot 'scripts\qemu3dfx_package.py') verify-launch --image $imagePath --qemu (Join-Path $bundlePath 'host\qemu-system-x86_64.exe')
if ($LASTEXITCODE -ne 0) { throw 'Vorbereitungsbeleg ungültig.' }
Write-Output 'QEMU-3dfx-Arbeitskopie vorbereitet. Es wurde keine VM gestartet.'

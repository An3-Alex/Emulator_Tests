$ErrorActionPreference = 'Stop'

function Find-Python {
    foreach ($commandName in @('python.exe', 'py.exe')) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $candidate = $command.Source
        try {
            if ($commandName -eq 'py.exe') {
                & $candidate -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            } else {
                & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            }
            if ($LASTEXITCODE -eq 0) { return @($candidate, $commandName) }
        } catch { continue }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Add-Type -AssemblyName System.Windows.Forms
    $choice = [System.Windows.Forms.MessageBox]::Show(
        'Python 3.10 oder neuer fehlt. Jetzt über den Windows-Paketmanager installieren? Anschließend dieses Startprogramm erneut öffnen.',
        'M90 Emulator – Python',
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question
    )
    if ($choice -ne [System.Windows.Forms.DialogResult]::Yes) { exit 1 }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Write-Error 'winget wurde nicht gefunden. Python bitte manuell installieren.'
    }
    & winget.exe install --exact --id Python.Python.3.14 --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output 'Python installiert. Bitte dieses Startprogramm erneut öffnen.'
    exit 0
}

$launcher = Join-Path $PSScriptRoot 'scripts\emulator_launcher.py'
if ($python[1] -eq 'py.exe') {
    & $python[0] -3 $launcher
} else {
    & $python[0] $launcher
}
exit $LASTEXITCODE

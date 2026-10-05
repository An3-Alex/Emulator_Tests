param(
    [Parameter(Mandatory = $true)][string]$Port,
    [string]$Qemu = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$Image,
    [switch]$UsbTablet,
    [switch]$NoEventWindow,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if ($Port -notmatch '^COM[1-9][0-9]*$') {
    throw 'Bitte den nativen USB-COM-Port des ESP32 angeben, zum Beispiel -Port COM7.'
}
if (-not $DryRun -and [string]::IsNullOrWhiteSpace($Image)) {
    throw 'CF-Image: Datei auswählen'
}

$visibleLauncher = Join-Path $PSScriptRoot 'test-swiftshader.ps1'
$relayScript = Join-Path $PSScriptRoot 'scripts\esp32_com3_relay.py'
if ($DryRun) {
    $visiblePlan = (& $visibleLauncher -Qemu $Qemu -Image $Image -UsbTablet:$UsbTablet -DryRun |
        ConvertFrom-Json)
    [ordered]@{
        mode = 'esp32-usb-database'
        visible_qemu = $visiblePlan
        serial_port = $Port
        relay = $relayScript
        guest_com3 = 'tcp:127.0.0.1:4553'
        virtual_database_bridge = 'not started'
        original_start_script = 'start-real-database.ps1 remains unchanged'
    } | ConvertTo-Json -Depth 6 -Compress
    return
}

& python -c 'import serial'
if ($LASTEXITCODE -ne 0) {
    throw 'pyserial fehlt. Den ESP32-Flasher starten oder python -m pip install pyserial verwenden.'
}
if (-not (Test-Path -LiteralPath $relayScript)) {
    throw "ESP32-Relay fehlt: $relayScript"
}
& python -u $relayScript --port $Port --probe-only
if ($LASTEXITCODE -ne 0) {
    throw "ESP32-Port $Port ist nicht verfügbar; QEMU wurde nicht gestartet."
}

$qemuLaunchOutput = @(& $visibleLauncher -Qemu $Qemu -Image $Image -UsbTablet:$UsbTablet)
$qemuLaunchOutput | Write-Output
$qemuPidLine = $qemuLaunchOutput | Where-Object { $_ -match '^QEMU_PID=\d+$' } |
    Select-Object -First 1
if (-not $qemuPidLine) {
    throw 'QEMU-Start hat keine Prozess-ID geliefert.'
}
$qemuPid = [int]($qemuPidLine -replace '^QEMU_PID=', '')

$logDirectory = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$eventLogPath = Join-Path $logDirectory ('esp32-database-events-{0:yyyyMMdd-HHmmss}.log' -f (Get-Date))
$viewer = $null
$relayExitCode = 1
try {
    if (-not $NoEventWindow) {
        $pythonCommand = (Get-Command python.exe -ErrorAction Stop).Source
        $pythonw = Join-Path (Split-Path $pythonCommand -Parent) 'pythonw.exe'
        if (Test-Path -LiteralPath $pythonw) {
            $viewerScript = Join-Path $PSScriptRoot 'scripts\event_log_viewer.py'
            $viewerArgs = ('"{0}" --log "{1}"' -f $viewerScript, $eventLogPath)
            $viewer = Start-Process -FilePath $pythonw -ArgumentList $viewerArgs `
                -WindowStyle Hidden -PassThru
        }
    }
    Write-Output "ESP32 USB database mode; QEMU PID=$qemuPid; event log=$eventLogPath"
    Write-Output 'The original virtual database bridge is NOT running. Use start-real-database.ps1 to return to the previous mode.'
    & python -u $relayScript --port $Port --log-file $eventLogPath
    $relayExitCode = $LASTEXITCODE
}
finally {
    if ($null -ne $viewer -and -not $viewer.HasExited) {
        Stop-Process -Id $viewer.Id
    }
    $startedQemu = Get-Process -Id $qemuPid -ErrorAction SilentlyContinue
    if ($null -ne $startedQemu -and $startedQemu.ProcessName -like 'qemu-system*') {
        Stop-Process -Id $qemuPid
        Write-Output "ESP32-QEMU beendet (PID $qemuPid); normaler Datenbankmodus wieder startbar."
    }
}
exit $relayExitCode

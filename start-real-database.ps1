param(
    [string]$Qemu = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$QemuM68k = 'C:\Program Files\qemu\qemu-system-m68k.exe',
    [string]$Python = 'python',
    [string]$Image,
    [ValidateRange(512, 3072)][int]$GuestRamMiB = 2048,
    [ValidateRange(1, 2)][int]$GuestVcpus = 1,
    [ValidateSet('whpx', 'tcg')][string]$Acceleration = 'whpx',
    [ValidateSet(64, 128, 256)][int]$QxlVramMiB = 64,
    [string]$Database,
    [string]$Loader,
    [string]$FactoryReset,
    [string]$Config,
    [datetime]$DatabaseDate = [datetime]'2012-02-01T22:14:00',
    [Nullable[uint32]]$D3 = [uint32]::Parse('D27B7159', [Globalization.NumberStyles]::HexNumber),
    [string]$RuntimeDump,
    [string]$AdmissionEeprom,
    [switch]$DoorOpen,
    [switch]$NoEventWindow,
    [switch]$NoControlWindow,
    # icount plus multi-instruction TBs intermittently aborts in QEMU's
    # interrupt handler during INITVIDEO. Prefer the stable CPU mode.
    [switch]$SafeTb = $true,
    [switch]$FastTb,
    [switch]$UsbTablet,
    [switch]$SwapDisplays,
    [switch]$MuteAudio,
    [switch]$AudioBridge = $true,
    [ValidateSet(5, 6)][int]$DbIcountShift = 6,
    [ValidateRange(0.005, 0.05)][double]$DbTimerInterval = 0.01,
    [ValidateRange(1, 10000000)][int]$DuartX1Hz = 3686400,
    [ValidateRange(10, 600)][double]$DbConnectTimeout = 120,
    [switch]$TraceDiagnostics,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (-not $AudioBridge) { throw 'Nur PCM-Bridge wird unterstützt.' }
if ($DatabaseDate.Year -lt 2000 -or $DatabaseDate.Year -gt 2099) {
    throw 'RTC calendar requires year 2000..2099.'
}
if ([math]::Ceiling($DbTimerInterval * $DuartX1Hz / (32 * 58)) -gt 128) {
    throw 'DUART clock and run slice exceed the 128-interrupt budget. Reduce DbTimerInterval.'
}
if ($FastTb) { $SafeTb = $false }
$visibleOptions = @{
    GuestRamMiB = $GuestRamMiB; GuestVcpus = $GuestVcpus
    Acceleration = $Acceleration; QxlVramMiB = $QxlVramMiB
    MuteAudio = $MuteAudio
    AudioBridge = $AudioBridge; Python = $Python
}
if (($null -eq $D3) -eq [string]::IsNullOrWhiteSpace($RuntimeDump)) {
    throw 'Specify exactly one of -D3 or -RuntimeDump. The raw flash files alone do not contain the boot-ROM D3 value.'
}

$visibleLauncher = Join-Path $PSScriptRoot 'test-swiftshader.ps1'
function Get-SelectedInputHash([string]$Path) {
    if ($DryRun -and ([string]::IsNullOrWhiteSpace($Path) -or
        -not (Test-Path -LiteralPath $Path -PathType Leaf))) { return ('0' * 64) }
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = [IO.File]::OpenRead($Path)
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '')
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        $algorithm.Dispose()
    }
}
$arguments = @(
    (Join-Path $PSScriptRoot 'scripts\m68k_database_bridge.py'),
    '--loader', $Loader,
    '--expected-loader-sha256', (Get-SelectedInputHash $Loader),
    '--database', $Database,
    '--expected-database-sha256', (Get-SelectedInputHash $Database),
    '--config', $Config,
    '--expected-config-sha256', (Get-SelectedInputHash $Config),
    '--timer-interval', $DbTimerInterval.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--duart-x1-hz', [string]$DuartX1Hz,
    '--connect-timeout', $DbConnectTimeout.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--rtc-date', $DatabaseDate.ToString('yyyy-MM-ddTHH:mm:ss'),
    '--icount-shift', [string]$DbIcountShift,
    '--control-port', '4554'
    '--qemu', $QemuM68k
)
if (-not $SafeTb) { $arguments += '--fast-tb' }
if (-not [string]::IsNullOrWhiteSpace($Image)) {
    $arguments += @('--touch-state', ($Image + '.touch.json'))
}
if (-not [string]::IsNullOrWhiteSpace($FactoryReset)) {
    $arguments += @('--factory', $FactoryReset, '--expected-factory-sha256', (Get-SelectedInputHash $FactoryReset))
}
if ($TraceDiagnostics) { $arguments += '--trace-diagnostics' }
if ($DoorOpen) { $arguments += '--door-open' }
if (-not [string]::IsNullOrWhiteSpace($AdmissionEeprom)) {
    $arguments += @('--admission-eeprom', $AdmissionEeprom)
}
if ($null -ne $D3) { $arguments += @('--d3', ('0x{0:X8}' -f $D3)) }
else { $arguments += @('--runtime-dump', $RuntimeDump) }

if ($DryRun) {
    $visiblePlan = (& $visibleLauncher -Qemu $Qemu -Image $Image @visibleOptions -DryRun -UsbTablet:$UsbTablet -SwapDisplays:$SwapDisplays | ConvertFrom-Json)
    [ordered]@{
        visible_qemu = $visiblePlan
        database_bridge = [ordered]@{
            executable = 'python'
            arguments = $arguments
            timer_run_seconds = $DbTimerInterval
            duart_x1_hz = $DuartX1Hz
            connect_timeout = $DbConnectTimeout
            host_pause_seconds = 0.0
            emulated_db_icount_shift = $DbIcountShift
            emulated_db_max_instructions_per_second = [int](1000000000 / [math]::Pow(2, $DbIcountShift))
            m68k_tcg_mode = if ($SafeTb) { 'single-instruction fallback' } else { 'translation-block fast mode' }
            diagnostic_watchpoints = [bool]$TraceDiagnostics
            windows_priority = 'Normal'
            door_switch = if ($DoorOpen) { 'open' } else { 'closed' }
        }
        event_window = [ordered]@{
            visible = -not $NoEventWindow
            viewer = 'scripts/event_log_viewer.py'
            source = 'live bridge log in logs/'
        }
        control_window = [ordered]@{
            visible = -not $NoControlWindow
            panel = 'scripts/cabinet_control_panel.py'
            touch = 'lower cabinet display and QEMU mouse'
            buttons = @('menu', 'autostart', 'einsatz', 'maxeinsatz', 'start', 'auszahlung', 'service')
            door_switch = $true
            port = 4554
        }
    } | ConvertTo-Json -Depth 6 -Compress
    return
}

foreach ($item in @(
    @{ Name = 'Image'; Value = $Image },
    @{ Name = 'Database'; Value = $Database },
    @{ Name = 'Loader'; Value = $Loader },
    @{ Name = 'Config'; Value = $Config }
)) {
    if ([string]::IsNullOrWhiteSpace($item.Value)) {
        throw "Datei auswählen: $($item.Name)"
    }
}

$audioReceiver = $null
$audioReceiverStarted = $false
try {
if ($AudioBridge) {
    $pythonPath = (Get-Command $Python -ErrorAction Stop).Source
    $audioLogDirectory = Join-Path $PSScriptRoot 'logs'
    New-Item -ItemType Directory -Path $audioLogDirectory -Force | Out-Null
    $stamp = [guid]::NewGuid().ToString('N')
    $readyFile = Join-Path $audioLogDirectory "audio-bridge-$stamp.ready"
    $audioLog = Join-Path $audioLogDirectory "audio-bridge-$stamp.jsonl"
    $audioScript = Join-Path $PSScriptRoot 'scripts\pcm_audio_bridge.py'
    $sdl = Join-Path (Split-Path $Qemu -Parent) 'SDL2.dll'
    if (-not $MuteAudio -and -not (Test-Path -LiteralPath $sdl -PathType Leaf)) {
        throw "SDL2.dll neben QEMU fehlt: $sdl"
    }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $pythonPath
    $info.Arguments = ('-u "{0}" --sdl "{1}" --log "{2}" --ready-file "{3}"' -f $audioScript, $sdl, $audioLog, $readyFile)
    if ($MuteAudio) { $info.Arguments += ' --muted' }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $audioReceiver = New-Object Diagnostics.Process
    $audioReceiver.StartInfo = $info
    if (-not $audioReceiver.Start()) { throw 'Audio-Bridge konnte nicht starten' }
    $audioReceiverStarted = $true
    $deadline = [datetime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $readyFile -PathType Leaf)) {
        if ($audioReceiver.HasExited) { throw "Audio-Bridge beendet (Code $($audioReceiver.ExitCode)); siehe $audioLog" }
        if ([datetime]::UtcNow -gt $deadline) { throw 'Audio-Bridge meldet keine Bereitschaft' }
        Start-Sleep -Milliseconds 100
    }
    Write-Output "AUDIO_BRIDGE_PID=$($audioReceiver.Id) log=$audioLog"
}
$qemuLaunchOutput = @(& $visibleLauncher -Qemu $Qemu -Image $Image @visibleOptions -UsbTablet:$UsbTablet -SwapDisplays:$SwapDisplays)
$qemuLaunchOutput | Write-Output
$qemuPidLine = $qemuLaunchOutput | Where-Object { $_ -match '^QEMU_PID=\d+$' } | Select-Object -First 1
if (-not $qemuPidLine) {
    throw 'The visible launcher did not report a QEMU process ID.'
}
$qemuPid = [int]($qemuPidLine -replace '^QEMU_PID=', '')

Write-Output 'QEMU is visible with lower/upper tabs. The real owner database firmware is connected to guest COM3.'
Write-Output 'Guest reboots remain enabled. UART traffic is written to the live event log.'
$logDirectory = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$eventLogPath = Join-Path $logDirectory ('database-events-{0:yyyyMMdd-HHmmss}.log' -f (Get-Date))
$viewer = $null
$controlPanel = $null
if (-not $NoEventWindow -or -not $NoControlWindow) {
    $pythonCommand = (Get-Command $Python -ErrorAction Stop).Source
    $pythonw = Join-Path (Split-Path $pythonCommand -Parent) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) {
        Write-Warning 'pythonw.exe was not found; GUI windows cannot be started.'
    }
    else {
        if (-not $NoEventWindow) {
            $viewerScript = Join-Path $PSScriptRoot 'scripts\event_log_viewer.py'
            $viewerArgs = ('"{0}" --log "{1}"' -f $viewerScript, $eventLogPath)
            $viewer = Start-Process -FilePath $pythonw -ArgumentList $viewerArgs -PassThru
        }
        if (-not $NoControlWindow) {
            $controlScript = Join-Path $PSScriptRoot 'scripts\cabinet_control_panel.py'
            $capturePath = Join-Path $logDirectory 'cabinet-lower.png'
            $controlArgs = ('"{0}" --qemu-pid {1} --capture "{2}"' -f $controlScript, $qemuPid, $capturePath)
            if ($DoorOpen) { $controlArgs += ' --door-open' }
            $controlPanel = Start-Process -FilePath $pythonw -ArgumentList $controlArgs -PassThru
            Write-Output "Cabinet control window PID=$($controlPanel.Id)"
        }
    }
}
Write-Output "Database event log: $eventLogPath"
$arguments += @('--log-file', $eventLogPath)
try {
    # The bridge writes line-buffered UTF-8 directly to disk. PowerShell's
    # per-line Tee pipeline used significant host CPU during startup.
    & $Python -u @arguments
    $bridgeExitCode = $LASTEXITCODE
}
finally {
    if ($null -ne $viewer -and -not $viewer.HasExited) {
        Stop-Process -Id $viewer.Id
    }
    if ($null -ne $controlPanel -and -not $controlPanel.HasExited) {
        Stop-Process -Id $controlPanel.Id
    }
}
} finally {
    if ($null -ne $audioReceiver) {
        if ($audioReceiverStarted -and -not $audioReceiver.HasExited) {
            $audioReceiver.StandardInput.Close()
            if (-not $audioReceiver.WaitForExit(6000)) { $audioReceiver.Kill(); $audioReceiver.WaitForExit() }
        }
        $audioReceiver.Dispose()
    }
}
exit $bridgeExitCode

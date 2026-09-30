param(
    [string]$Qemu = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$QemuM68k = 'C:\Program Files\qemu\qemu-system-m68k.exe',
    [string]$Python = 'python',
    [string]$Image,
    [string]$Database,
    [string]$Loader,
    [string]$Config,
    [Nullable[uint32]]$D3 = [uint32]::Parse('D27B7159', [Globalization.NumberStyles]::HexNumber),
    [string]$RuntimeDump,
    [string]$AdmissionEeprom,
    [switch]$DoorOpen,
    [switch]$NoEventWindow,
    [switch]$NoControlWindow,
    # icount plus multi-instruction TBs intermittently aborts in QEMU's
    # interrupt handler during INITVIDEO. Prefer the stable CPU mode.
    [switch]$SafeTb = $true,
    [switch]$UsbTablet,
    [switch]$SwapDisplays,
    [ValidateSet(5, 6)][int]$DbIcountShift = 6,
    [switch]$TraceDiagnostics,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (($null -eq $D3) -eq [string]::IsNullOrWhiteSpace($RuntimeDump)) {
    throw 'Specify exactly one of -D3 or -RuntimeDump. The raw flash files alone do not contain the boot-ROM D3 value.'
}

$visibleLauncher = Join-Path $PSScriptRoot 'test-swiftshader.ps1'
$arguments = @(
    (Join-Path $PSScriptRoot 'scripts\m68k_database_bridge.py'),
    '--loader', $Loader,
    '--expected-loader-sha256', 'B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109',
    '--database', $Database,
    '--expected-database-sha256', '593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8',
    '--config', $Config,
    '--expected-config-sha256', 'DCE3A865B742123C95EA4F0B14FA16F287DDF90CD86432F68B2301B70A919783',
    '--timer-interval', '0.05',
    '--icount-shift', [string]$DbIcountShift,
    '--control-port', '4554'
    '--qemu', $QemuM68k
)
if (-not $SafeTb) { $arguments += '--fast-tb' }
if ($TraceDiagnostics) { $arguments += '--trace-diagnostics' }
if ($DoorOpen) { $arguments += '--door-open' }
if (-not [string]::IsNullOrWhiteSpace($AdmissionEeprom)) {
    $arguments += @('--admission-eeprom', $AdmissionEeprom)
}
if ($null -ne $D3) { $arguments += @('--d3', ('0x{0:X8}' -f $D3)) }
else { $arguments += @('--runtime-dump', $RuntimeDump) }

if ($DryRun) {
    $visiblePlan = (& $visibleLauncher -Qemu $Qemu -Image $Image -DryRun -UsbTablet:$UsbTablet -SwapDisplays:$SwapDisplays | ConvertFrom-Json)
    [ordered]@{
        visible_qemu = $visiblePlan
        database_bridge = [ordered]@{
            executable = 'python'
            arguments = $arguments
            timer_run_seconds = 0.05
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

$qemuLaunchOutput = @(& $visibleLauncher -Qemu $Qemu -Image $Image -UsbTablet:$UsbTablet -SwapDisplays:$SwapDisplays)
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
exit $bridgeExitCode

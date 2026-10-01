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
    [switch]$SafeTb = $true,
    [switch]$FastTb,
    [switch]$UsbTablet,
    [switch]$DoorOpen,
    [switch]$TraceDiagnostics,
    [switch]$NoControlWindow,
    [switch]$NoEventWindow,
    [switch]$SwapDisplays,
    [switch]$MuteAudio,
    [ValidateSet(5, 6)][int]$DbIcountShift = 6,
    [ValidateRange(0.005, 0.05)][double]$DbTimerInterval = 0.05,
    [ValidateRange(1, 10000000)][int]$DuartX1Hz = 3686400,
    [ValidateRange(10, 600)][double]$DbConnectTimeout = 120,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if ($DatabaseDate.Year -lt 2000 -or $DatabaseDate.Year -gt 2099) {
    throw 'RTC calendar requires year 2000..2099.'
}
if ([math]::Ceiling($DbTimerInterval * $DuartX1Hz / (32 * 58)) -gt 128) {
    throw 'DUART clock and run slice exceed the 128-interrupt budget. Reduce DbTimerInterval.'
}
$runtimeOptions = @{
    GuestRamMiB = $GuestRamMiB; GuestVcpus = $GuestVcpus
    Acceleration = $Acceleration; QxlVramMiB = $QxlVramMiB
    MuteAudio = $MuteAudio
    DbTimerInterval = $DbTimerInterval; DuartX1Hz = $DuartX1Hz
    DbConnectTimeout = $DbConnectTimeout; FastTb = $FastTb
    DatabaseDate = $DatabaseDate
    UsbTablet = $UsbTablet; DoorOpen = $DoorOpen
    TraceDiagnostics = $TraceDiagnostics; NoControlWindow = $NoControlWindow
}
if (($null -eq $D3) -eq [string]::IsNullOrWhiteSpace($RuntimeDump)) {
    throw 'Specify exactly one of -D3 or -RuntimeDump. This is the original database boot-ROM context.'
}

$programmer = Join-Path $PSScriptRoot 'scripts\serialloader_chip_emulator.py'
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
$programArgs = @(
    $programmer,
    '--loader', $Loader,
    '--expected-loader-sha256', (Get-SelectedInputHash $Loader),
    '--factory', $FactoryReset,
    '--expected-factory-sha256', (Get-SelectedInputHash $FactoryReset),
    '--config', $Config,
    '--expected-config-sha256', (Get-SelectedInputHash $Config),
    '--database', $Database,
    '--expected-database-sha256', (Get-SelectedInputHash $Database),
    '--date', $DatabaseDate.ToString('yyyy-MM-ddTHH:mm:ss')
)
if ($null -ne $D3) { $programArgs += @('--d3', ('0x{0:X8}' -f $D3)) }
else { $programArgs += @('--runtime-dump', $RuntimeDump) }

$runtimeLauncher = Join-Path $PSScriptRoot 'start-real-database.ps1'
if ($DryRun) {
    if ($null -ne $D3) {
        $runtimePlan = (& $runtimeLauncher @runtimeOptions -Qemu $Qemu -QemuM68k $QemuM68k -Python $Python -Image $Image -Database $Database -Loader $Loader -Config $Config -D3 $D3 -AdmissionEeprom $AdmissionEeprom -SafeTb:$SafeTb -NoEventWindow:$NoEventWindow -DbIcountShift $DbIcountShift -SwapDisplays:$SwapDisplays -DryRun | ConvertFrom-Json)
    } else {
        $runtimePlan = (& $runtimeLauncher @runtimeOptions -Qemu $Qemu -QemuM68k $QemuM68k -Python $Python -Image $Image -Database $Database -Loader $Loader -Config $Config -D3 $null -RuntimeDump $RuntimeDump -AdmissionEeprom $AdmissionEeprom -SafeTb:$SafeTb -NoEventWindow:$NoEventWindow -DbIcountShift $DbIcountShift -SwapDisplays:$SwapDisplays -DryRun | ConvertFrom-Json)
    }
    [ordered]@{
        virtual_programming = [ordered]@{
            executable = $Python
            arguments = $programArgs
            date = $DatabaseDate.ToString('yyyy-MM-ddTHH:mm:ss')
        }
        runtime = $runtimePlan
    } | ConvertTo-Json -Depth 8 -Compress
    return
}

if (Get-Process qemu-system* -ErrorAction SilentlyContinue) {
    throw 'QEMU läuft bereits. Den vorhandenen Emulator erst beenden; die Datenbank wird nicht neu programmiert.'
}
foreach ($item in @(
    @{ Name = 'Image'; Value = $Image },
    @{ Name = 'Database'; Value = $Database },
    @{ Name = 'Loader'; Value = $Loader },
    @{ Name = 'FactoryReset'; Value = $FactoryReset },
    @{ Name = 'Config'; Value = $Config }
)) {
    if ([string]::IsNullOrWhiteSpace($item.Value)) {
        throw "Datei auswählen: $($item.Name)"
    }
}

Write-Output 'PROGRAMMING_VIRTUAL_DATABASE: Loader -> FactoryReset -> Date -> Config -> Database'
& $Python @programArgs
if ($LASTEXITCODE -ne 0) {
    throw "Virtual database programming failed with exit code $LASTEXITCODE"
}

if ($null -ne $D3) {
    & $runtimeLauncher @runtimeOptions -Qemu $Qemu -QemuM68k $QemuM68k -Python $Python -Image $Image -Database $Database -Loader $Loader -Config $Config -D3 $D3 -AdmissionEeprom $AdmissionEeprom -SafeTb:$SafeTb -NoEventWindow:$NoEventWindow -DbIcountShift $DbIcountShift -SwapDisplays:$SwapDisplays
} else {
    & $runtimeLauncher @runtimeOptions -Qemu $Qemu -QemuM68k $QemuM68k -Python $Python -Image $Image -Database $Database -Loader $Loader -Config $Config -D3 $null -RuntimeDump $RuntimeDump -AdmissionEeprom $AdmissionEeprom -SafeTb:$SafeTb -NoEventWindow:$NoEventWindow -DbIcountShift $DbIcountShift -SwapDisplays:$SwapDisplays
}
exit $LASTEXITCODE

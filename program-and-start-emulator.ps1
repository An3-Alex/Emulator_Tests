param(
    [string]$Qemu = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$QemuM68k = 'C:\Program Files\qemu\qemu-system-m68k.exe',
    [string]$Python = 'python',
    [string]$Image,
    [string]$Database,
    [string]$Loader,
    [string]$FactoryReset,
    [string]$Config,
    [datetime]$DatabaseDate = [datetime]'2012-02-01T22:14:00',
    [Nullable[uint32]]$D3 = [uint32]::Parse('D27B7159', [Globalization.NumberStyles]::HexNumber),
    [string]$RuntimeDump,
    [string]$AdmissionEeprom,
    [switch]$SafeTb = $true,
    [switch]$NoEventWindow,
    [switch]$SwapDisplays,
    [ValidateSet(5, 6)][int]$DbIcountShift = 6,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (($null -eq $D3) -eq [string]::IsNullOrWhiteSpace($RuntimeDump)) {
    throw 'Specify exactly one of -D3 or -RuntimeDump. This is the original database boot-ROM context.'
}

$programmer = Join-Path $PSScriptRoot 'scripts\serialloader_chip_emulator.py'
$programArgs = @(
    $programmer,
    '--loader', $Loader,
    '--expected-loader-sha256', 'B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109',
    '--factory', $FactoryReset,
    '--expected-factory-sha256', '4F088DB4AF5F4A5D112A003EF312EB19B4388C25902FFA03F75742379A0CD5C4',
    '--config', $Config,
    '--expected-config-sha256', 'DCE3A865B742123C95EA4F0B14FA16F287DDF90CD86432F68B2301B70A919783',
    '--database', $Database,
    '--expected-database-sha256', '593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8',
    '--date', $DatabaseDate.ToString('yyyy-MM-ddTHH:mm:ss')
)
if ($null -ne $D3) { $programArgs += @('--d3', ('0x{0:X8}' -f $D3)) }
else { $programArgs += @('--runtime-dump', $RuntimeDump) }

$runtimeLauncher = Join-Path $PSScriptRoot 'start-real-database.ps1'
if ($DryRun) {
    if ($null -ne $D3) {
        $runtimePlan = (& $runtimeLauncher -Qemu $Qemu -QemuM68k $QemuM68k -Python $Python -Image $Image -Database $Database -Loader $Loader -Config $Config -D3 $D3 -AdmissionEeprom $AdmissionEeprom -SafeTb:$SafeTb -NoEventWindow:$NoEventWindow -DbIcountShift $DbIcountShift -SwapDisplays:$SwapDisplays -DryRun | ConvertFrom-Json)
    } else {
        $runtimePlan = (& $runtimeLauncher -Qemu $Qemu -QemuM68k $QemuM68k -Python $Python -Image $Image -Database $Database -Loader $Loader -Config $Config -D3 $null -RuntimeDump $RuntimeDump -AdmissionEeprom $AdmissionEeprom -SafeTb:$SafeTb -NoEventWindow:$NoEventWindow -DbIcountShift $DbIcountShift -SwapDisplays:$SwapDisplays -DryRun | ConvertFrom-Json)
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
    & $runtimeLauncher -Qemu $Qemu -QemuM68k $QemuM68k -Python $Python -Image $Image -Database $Database -Loader $Loader -Config $Config -D3 $D3 -AdmissionEeprom $AdmissionEeprom -SafeTb:$SafeTb -NoEventWindow:$NoEventWindow -DbIcountShift $DbIcountShift -SwapDisplays:$SwapDisplays
} else {
    & $runtimeLauncher -Qemu $Qemu -QemuM68k $QemuM68k -Python $Python -Image $Image -Database $Database -Loader $Loader -Config $Config -RuntimeDump $RuntimeDump -AdmissionEeprom $AdmissionEeprom -SafeTb:$SafeTb -NoEventWindow:$NoEventWindow -DbIcountShift $DbIcountShift -SwapDisplays:$SwapDisplays
}
exit $LASTEXITCODE

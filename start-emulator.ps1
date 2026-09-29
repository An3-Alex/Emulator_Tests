param(
    [string]$Database = 'C:\Users\User\Desktop\Merkur DB\Magie_90_CC4.bin',
    [string]$Loader = 'C:\Users\User\Desktop\Merkur DB\Loader_61640403_L5.0b_2MB.bin',
    [string]$DatabaseSha256 = '593CF4B3A1CCC83F206E1492E44B9D303EA3C05990059B8659D8308DA1DC2EE8',
    [string]$LoaderSha256 = 'B0768C65B34834C7A740615D2B0ABDB470AEC012FE4DC4A3C11531EFA221E109',
    [string[]]$Modules = @(
        'C:\Users\User\Desktop\Merkur DB\FactoryReset_61640403.xc',
        'C:\Users\User\Desktop\Merkur DB\M90_Las_Vegas.bin',
        'C:\Users\User\Desktop\Merkur DB\M90_Multi_Juwel.bin'
    ),
    [string[]]$ModuleSha256 = @(
        '4F088DB4AF5F4A5D112A003EF312EB19B4388C25902FFA03F75742379A0CD5C4',
        'DCE3A865B742123C95EA4F0B14FA16F287DDF90CD86432F68B2301B70A919783',
        '445EF8CB754D5BCD8E8CE3901B90F0F32B53A9269F1CC2605F16119B3732A911'
    ),
    [switch]$PauseBeforeLargeCommand,
    [int]$StopAfterTick = 0,
    [int[]]$SkipTick = @()
)

$ErrorActionPreference = 'Stop'
$bridge = Join-Path $PSScriptRoot 'scripts\vidcom_init_bridge.py'
$pairValidator = Join-Path $PSScriptRoot 'scripts\validate_owner_database_set.py'
$prefixLog = Join-Path $PSScriptRoot 'logs\sram-compat-diagnostics-run2\LogFiles\VidComLog.5.part_01.txt'
$continuationLog = Join-Path $PSScriptRoot 'logs\sram-compat-diagnostics-run2\LogFiles\VidComLog.5.part_00.txt'
$runStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$serialLog = Join-Path $PSScriptRoot "logs\visible-emulator-$runStamp.log"

$validationArgs = @(
    $pairValidator, $Database, $Loader,
    '--expected-database-sha256', $DatabaseSha256,
    '--expected-loader-sha256', $LoaderSha256
)
if ($Modules.Count -ne $ModuleSha256.Count) {
    throw 'Each auxiliary module requires one SHA-256 pin'
}
for ($index = 0; $index -lt $Modules.Count; $index++) {
    $validationArgs += @('--module', $Modules[$index], '--expected-module-sha256', $ModuleSha256[$index])
}
& python @validationArgs
if ($LASTEXITCODE -ne 0) {
    throw "Owner database/loader validation failed with exit code $LASTEXITCODE"
}

& (Join-Path $PSScriptRoot 'test-swiftshader.ps1')

$bridgeArgs = @(
    $bridge,
    '--auto',
    '--connect-timeout', '120',
    '--log', $serialLog,
    '--owner-database', $Database,
    '--owner-loader', $Loader,
    '--owner-database-sha256', $DatabaseSha256,
    '--owner-loader-sha256', $LoaderSha256,
    '--owner-vidcom-log', $prefixLog,
    '--owner-vidcom-sha256', 'D58DF8BBF196CA7DC9B9B9F070FC8260E8F7FC64ABF8A658304E880A22794480',
    '--owner-vidcom-continuation', $continuationLog,
    '--owner-vidcom-continuation-sha256', '31E3211B1E96ABC9A4848636349FC28233BA4817AC4FD300AEDC504460194B9E'
)
for ($index = 0; $index -lt $Modules.Count; $index++) {
    $bridgeArgs += @('--owner-module', $Modules[$index], '--owner-module-sha256', $ModuleSha256[$index])
}
if ($PauseBeforeLargeCommand) {
    $bridgeArgs += @('--pause-before-tick', '321046')
}
if ($StopAfterTick -gt 0) {
    $bridgeArgs += @('--stop-after-tick', $StopAfterTick.ToString())
}
foreach ($tick in $SkipTick) {
    if ($tick -gt 0) {
        $bridgeArgs += @('--skip-owner-tick', $tick.ToString())
    }
}

Write-Output "SERIAL_LOG=$serialLog"
Write-Output 'QEMU is visible with upper/lower tabs; the bridge log remains in this console.'
Write-Output 'Guest reboots are allowed. Ctrl+C stops the bridge but does not kill QEMU.'
& python @bridgeArgs
exit $LASTEXITCODE

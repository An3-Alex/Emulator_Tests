param(
    [string]$Qemu = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$Image,
    [switch]$UsbTablet,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$qemuImage = $Image.Replace('\', '/')
$qemuArgs = '-accel whpx -machine pc -cpu "qemu32,+sse2,model-id=Intel(R) Celeron(R) M CPU 440 @ 1.86GHz" -smp 1 -m 2048 -drive file="' + $qemuImage + '",format=raw,if=ide,index=0,media=disk -boot c -vga none -device qxl-vga,id=upper,revision=2,vgamem_mb=64,xres=640,yres=480 -device qxl,id=lower,revision=2,vgamem_mb=64,xres=640,yres=480 -display gtk,show-tabs=on -netdev user,id=n0,restrict=on -device i82559c,netdev=n0,mac=00:13:95:06:EE:6E -serial null -serial null -serial tcp:127.0.0.1:4553,server=on,wait=off -serial null -qmp tcp:127.0.0.1:4444,server=on,wait=off'
if ($UsbTablet) { $qemuArgs += ' -usb -device usb-tablet' }

$launchPlan = [ordered]@{
    qemu = $Qemu
    image = $Image
    arguments = $qemuArgs
    guest_vcpus = 1
    guest_ram_mib = 2048
    host_priority = 'Normal'
    host_affinity_mask = $null
    visible = $true
    display_tabs = @('upper', 'lower')
    guest_pointer = if ($UsbTablet) { 'USB tablet (absolute coordinates)' } else { 'PS/2 mouse' }
    guest_reboots_allowed = $true
}
if ($DryRun) {
    $launchPlan | ConvertTo-Json -Depth 3 -Compress
    return
}

if (Get-Process qemu-system* -ErrorAction SilentlyContinue) {
    throw 'QEMU already running'
}
if (-not (Test-Path -LiteralPath $Qemu -PathType Leaf)) {
    throw "QEMU executable not found: $Qemu"
}
if (-not (Test-Path -LiteralPath $Image -PathType Leaf)) {
    throw "QEMU image not found: $Image"
}

$stderrLog = Join-Path $PSScriptRoot 'logs\swiftshader-qemu.stderr.log'
$vm = Start-Process $Qemu -ArgumentList $qemuArgs -WindowStyle Normal -PassThru -RedirectStandardError $stderrLog
Write-Output "QEMU_PID=$($vm.Id)"
Write-Output 'QEMU_CPU unrestricted_host=true guest_vcpus=1'

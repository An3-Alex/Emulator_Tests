param(
    [string]$Qemu = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$Image,
    [ValidateRange(512, 3072)][int]$GuestRamMiB = 2048,
    [ValidateRange(1, 2)][int]$GuestVcpus = 1,
    [ValidateSet('whpx', 'tcg')][string]$Acceleration = 'whpx',
    [ValidateSet(64, 128, 256)][int]$QxlVramMiB = 64,
    [switch]$UsbTablet,
    [switch]$SwapDisplays,
    [switch]$MuteAudio,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$qemuImage = $Image.Replace('\', '/')
$primaryDisplay = if ($SwapDisplays) { 'lower' } else { 'upper' }
$secondaryDisplay = if ($SwapDisplays) { 'upper' } else { 'lower' }
$qemuArgs = '-accel whpx -machine pc -cpu "qemu32,+sse2,model-id=Intel(R) Celeron(R) M CPU 440 @ 1.86GHz" -smp 1 -m 2048 -drive file="' + $qemuImage + '",format=raw,if=ide,index=0,media=disk -boot c -vga none -device qxl-vga,id=lower,revision=2,vgamem_mb=64,xres=640,yres=480 -device qxl,id=upper,revision=2,vgamem_mb=64,xres=640,yres=480 -display gtk,show-tabs=on -netdev user,id=n0,restrict=on -device i82559c,netdev=n0,mac=00:13:95:06:EE:6E -serial null -serial null -serial tcp:127.0.0.1:4553,server=on,wait=off -serial null -qmp tcp:127.0.0.1:4444,server=on,wait=off'
if ($UsbTablet) { $qemuArgs += ' -usb -device usb-tablet' }
$audioBackend = if ($MuteAudio) { 'none' } else { 'sdl' }
# Keep the emulated sound card present when muted. Guest WinMM must still
# create real sound objects; irrKlang's NULL driver cannot do that.
# DirectSound initializes host capture unconditionally and can abort before
# boot on PCs without a recording device. SDL opens playback independently.
# The original cabinet has one speaker. Mix guest channels to one host output.
$qemuArgs += " -audiodev $audioBackend,id=audio0,in.voices=0,out.channels=1 -device AC97,audiodev=audio0"
$qemuArgs = $qemuArgs.Replace('-accel whpx', "-accel $Acceleration").Replace('-smp 1 -m 2048', "-smp $GuestVcpus -m $GuestRamMiB").Replace('vgamem_mb=64', "vgamem_mb=$QxlVramMiB")
if (-not $SwapDisplays) {
    $qemuArgs = $qemuArgs.Replace('qxl-vga,id=lower', 'qxl-vga,id=upper').Replace('qxl,id=upper', 'qxl,id=lower')
}

$launchPlan = [ordered]@{
    qemu = $Qemu
    image = $Image
    arguments = $qemuArgs
    guest_vcpus = $GuestVcpus
    guest_ram_mib = $GuestRamMiB
    acceleration = $Acceleration
    qxl_vram_mib = $QxlVramMiB
    audio_card = 'AC97'
    audio_backend = $audioBackend
    audio_muted = [bool]$MuteAudio
    audio_recording = $false
    audio_output_channels = 1
    host_priority = 'Normal'
    host_affinity_mask = $null
    visible = $true
    display_tabs = @($primaryDisplay, $secondaryDisplay)
    swap_displays = [bool]$SwapDisplays
    cabinet_lower_device = 'lower'
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

function Assert-QemuStartup {
    param($Process, [string]$StderrPath)
    # This catches immediate option/backend failures, not full guest readiness.
    # Never publish a dead PID or start its database/control sidecars.
    if ($Process.WaitForExit(1500)) {
        $detail = if (Test-Path -LiteralPath $StderrPath -PathType Leaf) {
            (Get-Content -LiteralPath $StderrPath -Tail 20) -join [Environment]::NewLine
        } else { '' }
        throw "QEMU-Start fehlgeschlagen (Code $($Process.ExitCode)).`n$detail"
    }
}

$logDirectory = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$stderrLog = Join-Path $logDirectory 'swiftshader-qemu.stderr.log'
$vm = Start-Process $Qemu -ArgumentList $qemuArgs -WindowStyle Normal -PassThru -RedirectStandardError $stderrLog
Assert-QemuStartup -Process $vm -StderrPath $stderrLog
Write-Output "QEMU_PID=$($vm.Id)"
Write-Output "QEMU_AUDIO backend=$audioBackend card=AC97 recording=false muted=$([bool]$MuteAudio)"
Write-Output "QEMU_CPU unrestricted_host=true guest_vcpus=$GuestVcpus ram_mib=$GuestRamMiB acceleration=$Acceleration"
Write-Output "QEMU_DISPLAYS primary=$primaryDisplay secondary=$secondaryDisplay cabinet_preview=lower"

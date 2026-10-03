"""Boot the temporary XP QXL installer and wait for its serial completion signal."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import time

from qmp_capture import connect_with_retry


SETUP_OK = b"M90-QXL-SETUP-OK\n"
SETUP_FAILED = b"M90-QXL-SETUP-FAILED\n"
VERIFY_OK = b"M90-QXL-VERIFY-OK\n"
VERIFY_FAILED = b"M90-QXL-VERIFY-FAILED\n"


def qemu_command(qemu: Path, image: Path, serial_port: int, qmp_port: int, *, swap_displays: bool = False, gpu_runtime: bool = False) -> list[str]:
    primary, secondary = ("lower", "upper") if swap_displays else ("upper", "lower")
    command = [
        str(qemu), "-accel", "whpx,kernel-irqchip=off" if gpu_runtime else "whpx", "-machine", "pc",
        "-cpu", "qemu32,+sse2,model-id=Intel(R) Celeron(R) M CPU 440 @ 1.86GHz",
        "-smp", "1", "-m", "2048", "-drive",
        f"file={image.as_posix()},format=raw,if=ide,index=0,media=disk",
        "-boot", "c", "-vga", "none",
        "-device", f"qxl-vga,id={primary},revision=2,vgamem_mb=64,xres=640,yres=480",
        "-device", f"qxl,id={secondary},revision=2,vgamem_mb=64,xres=640,yres=480",
        "-display", "sdl,gl=off" if gpu_runtime else "gtk,show-tabs=on",
        "-netdev", "user,id=n0,restrict=on", "-device",
        "i82559c,netdev=n0,mac=00:13:95:06:EE:6E",
        "-serial", f"tcp:127.0.0.1:{serial_port},server=on,wait=off",
        "-serial", "null", "-serial", "null", "-serial", "null",
        "-qmp", f"tcp:127.0.0.1:{qmp_port},server=on,wait=off",
        "-no-reboot",
    ]
    if gpu_runtime:
        command.extend(["-name", "M90-3dfx-setup", "-L", str(qemu.parent / "pc-bios")])
    return command


def require_free_port(port: int) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def wait_for_signal(
    process: subprocess.Popen[bytes], port: int, timeout: int,
    success: bytes = SETUP_OK, failure: bytes = SETUP_FAILED,
) -> bytes:
    deadline = time.monotonic() + timeout
    connection: socket.socket | None = None
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"QEMU exited before installer result (code {process.returncode})")
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=1)
                connection.settimeout(1)
                break
            except OSError:
                time.sleep(0.5)
        if connection is None:
            raise TimeoutError("QEMU serial port did not open")
        print("QXL setup guest booting; waiting for installer result...", flush=True)
        received = bytearray()
        while time.monotonic() < deadline:
            try:
                chunk = connection.recv(4096)
            except socket.timeout:
                if process.poll() is not None:
                    raise RuntimeError(f"QEMU exited before installer result (code {process.returncode})")
                continue
            except ConnectionResetError as exc:
                raise RuntimeError("QEMU serial connection reset before installer result") from exc
            if not chunk:
                raise RuntimeError(
                    f"QEMU serial connection closed before installer result (code {process.poll()})"
                )
            received.extend(chunk)
            if success in received:
                return success
            if failure in received:
                return failure
            if len(received) > 8192:
                del received[:-1024]
        raise TimeoutError("QXL guest did not report completion before timeout")
    finally:
        if connection is not None:
            connection.close()


def wait_for_clean_shutdown(process: subprocess.Popen[bytes], qmp_port: int) -> None:
    if process.poll() is None:
        print("QXL installation complete; requesting ACPI powerdown...", flush=True)
        qmp = connect_with_retry("127.0.0.1", qmp_port, timeout=10)
        try:
            qmp.execute("system_powerdown")
        finally:
            qmp.close()
    try:
        result = process.wait(timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "QEMU is still running. The image must not be mounted or finalized until it stops."
        ) from exc
    if result != 0:
        raise RuntimeError(f"QEMU setup guest ended with code {result}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--stderr-log", type=Path, required=True)
    parser.add_argument("--serial-port", type=int, default=4554)
    parser.add_argument("--qmp-port", type=int, default=4446)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-boots", type=int, default=4)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--swap-displays", action="store_true")
    parser.add_argument("--gpu-runtime", action="store_true")
    args = parser.parse_args()
    if not args.qemu.is_file() or not args.image.is_file():
        parser.error("QEMU executable and staged image must exist")
    require_free_port(args.serial_port)
    require_free_port(args.qmp_port)
    args.stderr_log.parent.mkdir(parents=True, exist_ok=True)
    command = qemu_command(args.qemu, args.image, args.serial_port, args.qmp_port,
                           swap_displays=args.swap_displays, gpu_runtime=args.gpu_runtime)
    success = VERIFY_OK if args.verify else SETUP_OK
    failure = VERIFY_FAILED if args.verify else SETUP_FAILED
    with args.stderr_log.open("ab") as stderr_file:
        for attempt in range(1, args.max_boots + 1):
            print(f"QXL {'verification' if args.verify else 'setup'} boot {attempt}/{args.max_boots}", flush=True)
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr_file)
            try:
                result = wait_for_signal(process, args.serial_port, args.timeout, success, failure)
            except (RuntimeError, TimeoutError) as exc:
                try:
                    exit_code = process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    raise RuntimeError(
                        f"QEMU setup guest (PID {process.pid}) is still running after: {exc}. "
                        "Close it cleanly before inspecting or resuming this image."
                    ) from exc
                if exit_code == 0 and attempt < args.max_boots:
                    print("Guest rebooted before setup result; continuing next boot.", flush=True)
                    continue
                raise RuntimeError(f"QXL setup ended without success (QEMU code {exit_code})") from exc
            if result != success:
                wait_for_clean_shutdown(process, args.qmp_port)
                if args.verify:
                    print("QXL displays still inactive; driver retry required.", flush=True)
                    return 17
                raise RuntimeError("QXL guest reported incomplete driver installation")
            print("QXL guest reports both displays active." if args.verify
                  else "QXL guest reports both displays installed.", flush=True)
            wait_for_clean_shutdown(process, args.qmp_port)
            break
    print("QXL setup guest stopped cleanly.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Own one emulator process tree; never terminate by executable name."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
import socket
import subprocess
import threading


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class WindowsJob:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Emulator-Prozessverwaltung benötigt Windows")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.api, name); function.argtypes = args; function.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        self.lock = threading.Lock()
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error(); self.close(); raise ctypes.WinError(error)

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def pids(self):
        with self.lock:
            if not self.handle:
                return set()
            size = 4096
            while size <= 1024 * 1024:
                buffer = ctypes.create_string_buffer(size)
                if self.api.QueryInformationJobObject(self.handle, 3, buffer, size, None):
                    count = wintypes.DWORD.from_buffer(buffer, 4).value
                    return set((ctypes.c_size_t * count).from_buffer(buffer, 8))
                if ctypes.get_last_error() != 234:
                    raise ctypes.WinError(ctypes.get_last_error())
                size *= 2
            raise RuntimeError("Emulator-Prozessliste ist unerwartet groß")

    def close(self):
        with self.lock:
            if self.handle:
                handle, self.handle = self.handle, None
                self.api.CloseHandle(handle)


def listener_pid(port: int) -> int | None:
    """Check ownership before sending shutdown to a localhost QMP socket."""
    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                    wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    result = api(None, ctypes.byref(size), False, socket.AF_INET, 3, 0)
    if result != 122:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if api(buffer, ctypes.byref(size), False, socket.AF_INET, 3, 0):
        return None
    count = wintypes.DWORD.from_buffer(buffer).value
    for index in range(count):
        row = (wintypes.DWORD * 6).from_buffer(buffer, 4 + index * 24)
        if socket.ntohs(row[2] & 0xFFFF) == port and row[1] == int.from_bytes(socket.inet_aton("127.0.0.1"), "little"):
            return row[5]
    return None


class EmulatorProcesses:
    def __init__(self, process, job):
        self.process, self.job = process, job
        self.qemu_pid = None

    @classmethod
    def launch(cls, command, **kwargs):
        job = WindowsJob()
        process = None
        try:
            # The script cannot spawn children until it is inside our Job.
            process = subprocess.Popen([*command, "-WaitForHostStart"], stdin=subprocess.PIPE, **kwargs)
            job.assign(process)
            process.stdin.write("M90-START\n"); process.stdin.flush(); process.stdin.close()
            return cls(process, job)
        except BaseException:
            if process is not None and process.poll() is None:
                process.terminate(); process.wait(timeout=5)
            job.close()
            raise

    def stop(self, timeout=20):
        """Try XP ACPI shutdown, then clean up only this Job's descendants."""
        graceful = False
        try:
            if self.qemu_pid in self.job.pids() and listener_pid(4444) == self.qemu_pid:
                from qmp_capture import QmpClient
                qmp = QmpClient("127.0.0.1", 4444)
                try:
                    qmp.execute("system_powerdown")
                finally:
                    qmp.close()
                try:
                    self.process.wait(timeout=timeout)
                    graceful = True
                except subprocess.TimeoutExpired:
                    pass
        except (OSError, EOFError, RuntimeError):
            pass
        finally:
            self.close()
        self.process.wait(timeout=5)
        return graceful

    def close(self):
        self.job.close()

import os
import ctypes
from ctypes import wintypes
import socket
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import emulator_processes as owned


class ProcessLifecycleTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows TCP ownership')
    def test_listener_owner_is_the_actual_process(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            self.assertEqual(owned.listener_pid(listener.getsockname()[1]), os.getpid())

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell start gate')
    def test_real_powershell_gate_and_packaged_start_script(self):
        command = ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-File',
                   str(Path(__file__).resolve().parents[1] / 'program-and-start-emulator.ps1'), '-DryRun']
        group = owned.EmulatorProcesses.launch(command, stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT, text=True)
        try:
            output = group.process.stdout.read()
            self.assertEqual(group.process.wait(timeout=30), 0, output)
            self.assertIn('database_bridge', output)
        finally:
            group.close(); group.process.stdout.close()

    def test_start_gate_opens_only_after_job_assignment(self):
        steps = []
        job = mock.Mock()
        job.assign.side_effect = lambda p: steps.append('assigned')
        process = mock.Mock()
        process.stdin.write.side_effect = lambda s: steps.append(s)
        with mock.patch.object(owned, 'WindowsJob', return_value=job), mock.patch.object(owned.subprocess, 'Popen', return_value=process) as popen:
            group = owned.EmulatorProcesses.launch(['powershell.exe', '-File', 'start.ps1'], text=True)
        self.assertEqual(steps, ['assigned', 'M90-START\n'])
        self.assertEqual(popen.call_args.args[0][-1], '-WaitForHostStart')
        self.assertIs(group.process, process)

    def test_assignment_failure_never_releases_start_gate(self):
        job = mock.Mock()
        job.assign.side_effect = OSError('job rejected')
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(owned, 'WindowsJob', return_value=job), mock.patch.object(owned.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(OSError, 'job rejected'):
                owned.EmulatorProcesses.launch(['start'])
        process.stdin.write.assert_not_called()
        process.terminate.assert_called_once()
        job.close.assert_called_once()

    def test_foreign_qmp_listener_never_receives_shutdown(self):
        job = mock.Mock()
        job.pids.return_value = {123}
        group = owned.EmulatorProcesses(mock.Mock(), job)
        group.qemu_pid = 123
        with mock.patch.object(owned, 'listener_pid', return_value=999), mock.patch('qmp_capture.QmpClient') as qmp:
            self.assertFalse(group.stop())
        qmp.assert_not_called()
        job.close.assert_called_once()

    def test_owned_qmp_gets_acpi_before_cleanup(self):
        job = mock.Mock()
        job.pids.return_value = {123}
        group = owned.EmulatorProcesses(mock.Mock(), job)
        group.qemu_pid = 123
        with mock.patch.object(owned, 'listener_pid', return_value=123), mock.patch('qmp_capture.QmpClient') as qmp:
            self.assertTrue(group.stop())
        qmp.return_value.execute.assert_called_once_with('system_powerdown')
        qmp.return_value.close.assert_called_once()
        job.close.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Objects')
    def test_real_job_stops_descendant_but_not_unrelated_process(self):
        job = owned.WindowsJob()
        outsider = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        parent = subprocess.Popen([sys.executable, '-u', '-c',
            'import subprocess,sys,time; sys.stdin.readline(); child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); print(child.pid,flush=True); time.sleep(60)'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            job.assign(parent)
            parent.stdin.write('go\n'); parent.stdin.flush()
            child_pid = int(parent.stdout.readline())
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            child_handle = api.OpenProcess(0x100000, False, child_pid)
            self.assertTrue(child_handle)
            self.assertTrue({parent.pid, child_pid}.issubset(job.pids()))
            self.assertNotIn(outsider.pid, job.pids())
            job.close(); parent.wait(timeout=5)
            try:
                self.assertEqual(api.WaitForSingleObject(child_handle, 5000), 0)
            finally:
                api.CloseHandle(child_handle)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and child_pid in job.pids():
                time.sleep(.05)
            self.assertEqual(job.pids(), set())
            self.assertIsNone(outsider.poll())
            job.close()  # idempotent
        finally:
            job.close()
            if parent.poll() is None: parent.terminate(); parent.wait(timeout=5)
            outsider.terminate(); outsider.wait(timeout=5)
            parent.stdin.close(); parent.stdout.close()


if __name__ == '__main__':
    unittest.main()

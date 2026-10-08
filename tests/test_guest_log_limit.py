"""Guest DLL logs: the shared header empties a log at 10 MB, compiled natively."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUEST_LOGS = {
    "cgos_shim.c": '"C:\\\\NVRAM\\\\cgos_shim.log"',
    "d3d9_proxy.cpp": "D3D9_PROXY_LOG",
    "irrklang_proxy.c": "path",
    "sram_compat.c": "SRAM_LOG_PATH",
}


class GuestLogLimitTests(unittest.TestCase):
    def test_every_guest_log_writer_checks_the_limit(self):
        for name, path in GUEST_LOGS.items():
            with self.subTest(source=name):
                source = (ROOT / "src" / name).read_text(encoding="utf-8")
                self.assertIn('#include "guest_log_limit.h"', source)
                self.assertIn(f"m90_limit_log_before_write({path}", source)

    def test_log_is_emptied_at_the_limit_and_append_handles_continue(self):
        compiler = os.environ.get("M90_TEST_CC") or shutil.which("gcc")
        if not compiler or os.name != "nt":
            self.skipTest("Windows C compiler not available")
        program = '#include "guest_log_limit.h"\n' + r'''
#include <stdio.h>
int main(int argc, char **argv) {
    const char *path = argv[1];
    DWORD written;
    HANDLE log = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    WIN32_FILE_ATTRIBUTE_DATA info;
    (void)argc;
    m90_limit_log_before_write(path, 5);             /* the first write always checks */
    GetFileAttributesExA(path, GetFileExInfoStandard, &info);
    printf("%lu ", info.nFileSizeLow);
    m90_limit_log(path);                             /* a repeated check changes nothing */
    GetFileAttributesExA(path, GetFileExInfoStandard, &info);
    printf("%lu ", info.nFileSizeLow);
    WriteFile(log, "after", 5, &written, NULL);      /* open append handle */
    CloseHandle(log);
    GetFileAttributesExA(path, GetFileExInfoStandard, &info);
    printf("%lu\n", info.nFileSizeLow);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="guest-log-") as directory:
            source = Path(directory) / "limit.c"
            source.write_text(program)
            binary = source.with_suffix(".exe")
            result = subprocess.run([compiler, "-std=c11", "-Wall", "-Werror", f"-I{ROOT / 'src'}",
                                     str(source), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            log = Path(directory) / "guest.log"

            def sizes(content: bytes) -> list[int]:
                log.write_bytes(content)
                output = subprocess.run([str(binary), str(log)], capture_output=True, text=True,
                                        timeout=30).stdout
                return [int(value) for value in output.split()]

            # Below the limit the log is kept; the open handle appends.
            self.assertEqual(sizes(b"x" * 1000), [1000, 1000, 1005])
            # At the limit it is emptied, and the open handle continues at the new end.
            self.assertEqual(sizes(b"x" * (10 * 1024 * 1024)), [0, 0, 5])


if __name__ == "__main__":
    unittest.main()

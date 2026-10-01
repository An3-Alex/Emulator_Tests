"""Historical shell tools keep explicit target guards without personal paths."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = (
    "check_ntfs.sh", "configure_boot_autochk.sh", "install_d3d9_proxy.sh",
    "install_game_single_d3d.sh", "install_game_swiftshader_adapter.sh",
    "install_irrklang_proxy.sh", "install_shim.sh", "install_sram_compat.sh",
    "install_swiftshader_eval.sh", "repair_ntfs.sh", "restore_game_original.sh",
)


class WorkingImageGuardTests(unittest.TestCase):
    def test_every_tool_checks_explicit_target_before_loop_attachment(self):
        for name in TOOLS:
            source = (ROOT / "scripts" / name).read_text()
            with self.subTest(name=name):
                self.assertIn('/working_image_guard.sh"', source)
                self.assertLess(source.index("require_working_image "),
                                source.index("losetup --find"))

    def test_guard_refuses_original_unapproved_paths_and_wrong_sizes(self):
        wsl = shutil.which("wsl.exe")
        if not wsl:
            self.skipTest("WSL bash required for shell guard execution")
        result = subprocess.run([wsl, "--exec", "wslpath", "-a",
                                 str(ROOT / "scripts/working_image_guard.sh")],
                                capture_output=True, text=True, timeout=15, check=True)
        guard = result.stdout.strip()
        code = '''set -eu
source "$1"
fixture_dir=$(mktemp -d /tmp/m90-guard-fixture.XXXXXX)
trap 'rm -f -- "$fixture_dir/original.img" "$fixture_dir/working.img" "$fixture_dir/alias.img"; rmdir -- "$fixture_dir"' EXIT
touch "$fixture_dir/original.img" "$fixture_dir/working.img"
stat() { printf '%s\\n' "$fixture_size"; }
fixture_size=16139354112
unset M90_WORK_IMAGE M90_ORIGINAL_IMAGE
if require_working_image "$fixture_dir/working.img"; then exit 11; fi
M90_WORK_IMAGE="$fixture_dir/working.img"
M90_ORIGINAL_IMAGE="$fixture_dir/original.img"
require_working_image "$fixture_dir/working.img"
if require_working_image "$fixture_dir/original.img"; then exit 12; fi
M90_WORK_IMAGE="$fixture_dir/original.img"
if require_working_image "$fixture_dir/original.img"; then exit 13; fi
ln "$fixture_dir/original.img" "$fixture_dir/alias.img"
M90_WORK_IMAGE="$fixture_dir/alias.img"
if require_working_image "$fixture_dir/alias.img"; then exit 14; fi
M90_WORK_IMAGE="$fixture_dir/working.img"
fixture_size=4096
if require_working_image "$fixture_dir/working.img"; then exit 15; fi
'''
        result = subprocess.run([wsl, "--exec", "bash", "-c", code, "guard", guard],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

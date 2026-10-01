#!/usr/bin/env bash
# Stage a second XP boot that proves both QXL displays are usable.
set -euo pipefail
[[ $# -eq 2 || ( $# -eq 3 && $3 == '--repair-ready' ) ]] || {
  echo 'usage: stage_display_verify.sh WORKING_IMAGE DISPLAY_VERIFY_EXE [--repair-ready]' >&2; exit 2;
}
image=$1
verifier=$2
repair_ready=${3:-}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mount_dir=$(mktemp -d /tmp/m90-display-verify.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
[[ -f "$image" && $(stat -c %s "$image") == 16139354112 ]] || {
  echo 'working image missing or wrong size' >&2; exit 3;
}
[[ -f "$verifier" && $(sha256sum "$verifier" | cut -d' ' -f1) == \
  7a9de0b1e050b512f2cba1f5672e92cc8ef59a6ad4a72761e8469282a1d8e145 ]] || {
  echo 'display verifier missing or unrecognized' >&2; exit 3;
}
loop_device=$(losetup --find --show --read-only --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1
if [[ "$repair_ready" == '--repair-ready' ]]; then
  [[ $(cat "$mount_dir/NVRAM/m90_setup_stage.txt") == 'stage=ready' ]] || {
    echo 'image is not a previously prepared copy' >&2; exit 3;
  }
  expected_shell=fcc3019fb0c890a6e252985ea2ca413110c527b256b6cb4d8360e97797fbc0ea
else
  [[ $(cat "$mount_dir/NVRAM/m90_setup_stage.txt") == 'stage=qxl-pnp' ]] || {
    echo 'image is not in QXL installation stage' >&2; exit 3;
  }
  expected_shell=0e043b8fd7199d813596704be1c481b3c5643941af1a7a6cc15e15fcd379c296
fi
shell_hash=$(sha256sum "$mount_dir/WINDOWS/explorer.exe" | cut -d' ' -f1)
[[ "$shell_hash" == "$expected_shell" ||
   ( "$repair_ready" == '--repair-ready' && "$shell_hash" == ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6 ) ]] || {
  echo 'unexpected active XP shell' >&2; exit 3;
}
grep -Fq 'Matched devices: 0x00000002' "$mount_dir/NVRAM/qxl_install.log"
grep -Fq 'Installed devices: 0x00000002' "$mount_dir/NVRAM/qxl_install.log"
umount "$mount_dir"
mounted=0
losetup -d "$loop_device"
loop_device=
loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
mounted=1
if [[ "$repair_ready" == '--repair-ready' ]]; then
  python3 "$script_dir/set_registry_dword.py" \
    "$mount_dir/WINDOWS/system32/config/SYSTEM" ControlSet001/Services/FBWF Start 4 --expect 0
fi
cp "$verifier" "$mount_dir/WINDOWS/explorer.exe.new"
mv "$mount_dir/WINDOWS/explorer.exe.new" "$mount_dir/WINDOWS/explorer.exe"
printf 'stage=qxl-verify\n' > "$mount_dir/NVRAM/m90_setup_stage.txt"
sync
echo 'QXL display verification boot staged.'

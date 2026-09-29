#!/usr/bin/env bash
# Run only after the temporary QXL guest has shut down cleanly.
set -euo pipefail

[[ $# -eq 2 ]] || { echo 'usage: finalize_image_stage.sh WORKING_IMAGE DISPLAY_BOOTSTRAP' >&2; exit 2; }
image=$1
bootstrap=$2
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mount_dir=$(mktemp -d /tmp/m90-finalize.XXXXXX)
loop_device=
mounted=0

cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT

verify_hash() {
  local file=$1 expected=$2 actual
  [[ -f "$file" ]] || { echo "missing file: $file" >&2; exit 3; }
  actual=$(sha256sum "$file" | cut -d' ' -f1)
  [[ "$actual" == "$expected" ]] || {
    echo "hash mismatch: $file ($actual)" >&2; exit 3;
  }
}

[[ -f "$image" && $(stat -c %s "$image") == 16139354112 ]] || {
  echo 'working image missing or wrong size' >&2; exit 3;
}
verify_hash "$bootstrap" ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6
loop_device=$(losetup --find --show --read-only --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1
verify_hash "$mount_dir/WINDOWS/explorer.exe" 0e043b8fd7199d813596704be1c481b3c5643941af1a7a6cc15e15fcd379c296
verify_hash "$mount_dir/WINDOWS/explorer_adp_before_qxl.exe" d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
[[ $(cat "$mount_dir/NVRAM/m90_setup_stage.txt") == 'stage=qxl-pnp' ]] || {
  echo 'image is not in QXL preparation stage' >&2; exit 3;
}
log="$mount_dir/NVRAM/qxl_install.log"
[[ -f "$log" ]] || { echo 'QXL installer log missing; setup guest has not finished' >&2; exit 3; }
grep -Fq 'Matched devices: 0x00000002' "$log" || {
  echo 'QXL installer did not find both displays' >&2; exit 3;
}
grep -Fq 'Installed devices: 0x00000002' "$log" || {
  echo 'QXL installer did not install both displays' >&2; exit 3;
}
python3 "$script_dir/hive_query.py" \
  "$mount_dir/WINDOWS/system32/config/SYSTEM" ControlSet001/Services/qxl --depth 1 \
  >/dev/null || { echo 'QXL service not registered' >&2; exit 3; }
umount "$mount_dir"
mounted=0
losetup -d "$loop_device"
loop_device=

loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
mounted=1
python3 "$script_dir/set_registry_dword.py" \
  "$mount_dir/WINDOWS/system32/config/SYSTEM" ControlSet001/Services/FBWF Start 0 --expect 4
cp "$bootstrap" "$mount_dir/WINDOWS/explorer.exe.new"
mv "$mount_dir/WINDOWS/explorer.exe.new" "$mount_dir/WINDOWS/explorer.exe"
printf 'stage=ready\n' > "$mount_dir/NVRAM/m90_setup_stage.txt"
sync
verify_hash "$mount_dir/WINDOWS/explorer.exe" ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6
echo "Prepared image ready: $image"

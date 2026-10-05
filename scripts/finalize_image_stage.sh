#!/usr/bin/env bash
# Run only after the temporary QXL guest has shut down cleanly.
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

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

[[ -f "$image" ]] || {
  echo 'working image missing' >&2; exit 3;
}
verify_hash "$bootstrap" fcc3019fb0c890a6e252985ea2ca413110c527b256b6cb4d8360e97797fbc0ea
loop_device=$(image_loop_device "$image" ro)
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1
verifier_hash=$(sha256sum "$mount_dir/WINDOWS/explorer.exe" | cut -d' ' -f1)
[[ "$verifier_hash" == 81e733743146b025d2f555ba476e1948be1d1515ba55465d8ba4418d4a193349 ||
   "$verifier_hash" == 7a9de0b1e050b512f2cba1f5672e92cc8ef59a6ad4a72761e8469282a1d8e145 ||
   "$verifier_hash" == aacd9215399d0122b46cb3b428dde15fad421de248e74e35c88b7de3645cc789 ]] || {
  echo 'unexpected active display verifier' >&2; exit 3;
}
verify_hash "$mount_dir/WINDOWS/explorer_adp_before_qxl.exe" d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
[[ $(cat "$mount_dir/NVRAM/m90_setup_stage.txt") == 'stage=qxl-verify' ]] || {
  echo 'image has not completed the QXL display verification boot' >&2; exit 3;
}
log="$mount_dir/NVRAM/qxl_install.log"
[[ -f "$log" ]] || { echo 'QXL installer log missing; setup guest has not finished' >&2; exit 3; }
grep -Fq 'Matched devices: 0x00000002' "$log" || {
  echo 'QXL installer did not find both displays' >&2; exit 3;
}
grep -Fq 'Installed devices: 0x00000002' "$log" || {
  echo 'QXL installer did not install both displays' >&2; exit 3;
}
verify_log="$mount_dir/NVRAM/display_verify.log"
[[ -f "$verify_log" ]] || { echo 'QXL display verification log missing' >&2; exit 3; }
for expected in \
  'Recognized QXL displays: 0x00000002' \
  'Active QXL primary: 0x00000001' \
  'Attached secondary displays: 0x00000001' \
  'Global apply result: 0x00000000'; do
  grep -Fq "$expected" "$verify_log" || {
    echo "QXL display verification failed: $expected" >&2; exit 3;
  }
done
python3 "$script_dir/hive_query.py" \
  "$mount_dir/WINDOWS/system32/config/SYSTEM" ControlSet001/Services/qxl --depth 1 \
  >/dev/null || { echo 'QXL service not registered' >&2; exit 3; }
umount "$mount_dir"
mounted=0
losetup -d "$loop_device"
loop_device=

loop_device=$(image_loop_device "$image" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
mounted=1
python3 "$script_dir/set_registry_dword.py" \
  "$mount_dir/WINDOWS/system32/config/SYSTEM" ControlSet001/Services/FBWF Start 0 --expect 4
cp "$bootstrap" "$mount_dir/WINDOWS/explorer.exe.new"
mv "$mount_dir/WINDOWS/explorer.exe.new" "$mount_dir/WINDOWS/explorer.exe"
printf 'stage=ready\n' > "$mount_dir/NVRAM/m90_setup_stage.txt"
sync
verify_hash "$mount_dir/WINDOWS/explorer.exe" fcc3019fb0c890a6e252985ea2ca413110c527b256b6cb4d8360e97797fbc0ea
echo "Prepared image ready: $image"

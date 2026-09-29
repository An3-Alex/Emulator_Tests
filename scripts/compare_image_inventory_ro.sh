#!/usr/bin/env bash
set -euo pipefail

[[ $# == 2 ]] || { echo 'usage: compare_image_inventory_ro.sh ORIGINAL WORKING' >&2; exit 2; }
original=$1
working=$2
temp_dir=$(mktemp -d /tmp/m90-inventory.XXXXXX)
mount_dir=$temp_dir/mount
mkdir "$mount_dir"
loop_device=
mounted=0

cleanup() {
  if (( mounted )); then umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rm -f -- "$temp_dir/original.files" "$temp_dir/working.files" \
    "$temp_dir/original.hashes" "$temp_dir/working.hashes"
  rmdir "$mount_dir" "$temp_dir"
}
trap cleanup EXIT

inventory() {
  local label=$1 image=$2 candidate
  [[ -f "$image" ]] || { echo "missing image: $image" >&2; exit 3; }
  loop_device=$(losetup --find --show --read-only --offset 1048576 \
    --sizelimit 16021151744 "$image")
  ntfs-3g -o ro "$loop_device" "$mount_dir"
  mounted=1
  find "$mount_dir" -type f -printf '%P\t%s\n' | LC_ALL=C sort > "$temp_dir/$label.files"
  : > "$temp_dir/$label.hashes"
  for candidate in \
    WINDOWS/system32/Cgos.dll WINDOWS/system32/ADPInterface.dll \
    WINDOWS/system32/fbwflib.dll WINDOWS/explorer.exe \
    WINDOWS/explorer_adp_before_qxl.exe \
    NVRAM/game.exe WorkDir/game.exe \
    NVRAM/FBWFLIB.dll WorkDir/FBWFLIB.dll \
    NVRAM/d3d9.dll WorkDir/d3d9.dll \
    NVRAM/swiftshader_d3d9.dll WorkDir/swiftshader_d3d9.dll \
    NVRAM/irrKlang.dll WorkDir/irrKlang.dll; do
    if [[ -f "$mount_dir/$candidate" ]]; then
      printf '%s\t' "$candidate" >> "$temp_dir/$label.hashes"
      sha256sum "$mount_dir/$candidate" | cut -d' ' -f1 >> "$temp_dir/$label.hashes"
    fi
  done
  umount "$mount_dir"
  mounted=0
  losetup -d "$loop_device"
  loop_device=
}

inventory original "$original"
inventory working "$working"
echo "ORIGINAL_FILES=$(wc -l < "$temp_dir/original.files")"
echo "WORKING_FILES=$(wc -l < "$temp_dir/working.files")"
echo 'FILE_LIST_OR_SIZE_DIFFERENCES (first 150):'
diff -u "$temp_dir/original.files" "$temp_dir/working.files" | head -n 154 || true
echo 'PROGRAM_AND_DRIVER_DIFFERENCES:'
diff -u "$temp_dir/original.files" "$temp_dir/working.files" \
  | grep -E '^[+-](WINDOWS|NVRAM|WorkDir)/.*\.(dll|exe|sys|inf|cfg|bin)([[:space:]]|$)' \
  | head -n 200 || true
echo 'KEY_FILE_HASHES_ORIGINAL:'
cat "$temp_dir/original.hashes"
echo 'KEY_FILE_HASHES_WORKING:'
cat "$temp_dir/working.hashes"

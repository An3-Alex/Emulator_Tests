#!/usr/bin/env bash
# Update only the selected prepared copy, with guest-file backups. No VM boot.
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"
[[ $# -eq 4 || $# -eq 5 ]] || { echo 'usage: update_runtime_graphics.sh ORIGINAL WORKING BOOTSTRAP D3D9 [AUDIO]' >&2; exit 2; }
audio_args=()
if [[ $# -eq 5 ]]; then audio_args=(--audio "$5"); fi
original=$(realpath "$1")
image=$(realpath "$2")
[[ -f "$original" && -f "$image" && "$original" != "$image" && ! "$original" -ef "$image" ]] || {
  echo 'Original and working copy must be separate existing files' >&2; exit 3;
}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mount_dir=$(mktemp -d /tmp/m90-graphics-update.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
mount_image() {
  local mode=$1
  if [[ "$mode" == ro ]]; then
    loop_device=$(image_loop_device "$image" ro)
    ntfs-3g -o ro "$loop_device" "$mount_dir"
  else
    loop_device=$(image_loop_device "$image" rw)
    ntfs-3g -o big_writes "$loop_device" "$mount_dir"
  fi
  mounted=1
}
mount_image ro
result=$(python3 "$script_dir/graphics_update.py" "$mount_dir" "$3" "$4" "${audio_args[@]}" --check-only)
echo "$result"
[[ "$result" != 'Graphics already current' && "$result" != 'Runtime already current' ]] || exit 0
umount "$mount_dir"
mounted=0
losetup -d "$loop_device"
loop_device=
mount_image rw
# Revalidate after remount, before any write, and preserve old versions first.
python3 "$script_dir/graphics_update.py" "$mount_dir" "$3" "$4" "${audio_args[@]}"
sync

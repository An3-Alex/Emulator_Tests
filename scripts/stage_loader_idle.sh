#!/usr/bin/env bash
# Update only the saved ADP loader in a separate, prepared working copy.
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"
[[ $# -eq 2 ]] || { echo 'usage: stage_loader_idle.sh ORIGINAL WORKING' >&2; exit 2; }
original=$(realpath "$1")
image=$(realpath "$2")
[[ -f "$original" && -f "$image" && "$original" != "$image" && ! "$original" -ef "$image" ]] || {
  echo 'Original and working copy must be separate existing files' >&2; exit 3;
}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mount_dir=$(mktemp -d /tmp/m90-loader-idle.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
loop_device=$(image_loop_device "$image" ro)
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1
result=$(python3 "$script_dir/loader_idle.py" "$mount_dir" --check-only)
echo "$result"
[[ "$result" == 'Loader idle wait update required' ]] || exit 0
umount "$mount_dir"
mounted=0
losetup -d "$loop_device"
loop_device=$(image_loop_device "$image" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
mounted=1
# Full loader hash and existing backup are checked again before writing.
python3 "$script_dir/loader_idle.py" "$mount_dir"
sync

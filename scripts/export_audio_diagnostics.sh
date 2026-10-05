#!/usr/bin/env bash
# Host has stopped the setup VM; collect only allowlisted logs, read-only.
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"
[[ $# -eq 3 ]] || exit 2
original=$(realpath "$1")
image=$(realpath "$2")
destination=$(realpath "$3")
[[ -f "$original" && -f "$image" && "$original" != "$image" && ! "$original" -ef "$image" ]] || {
  echo 'Original and working copy must be separate files' >&2; exit 3;
}
mount_dir=$(mktemp -d /tmp/m90-audio-diagnostics.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
loop_device=$(image_loop_device "$image" ro)
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1
script_dir=$(cd -- "$(dirname "$0")" && pwd)
python3 "$script_dir/audio_diagnostics.py" "$mount_dir" "$destination"

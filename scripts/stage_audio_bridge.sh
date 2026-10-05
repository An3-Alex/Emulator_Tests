#!/usr/bin/env bash
# Change only our backend marker in a stopped, separate working image.
set -euo pipefail
[[ $# -eq 3 && ( "$3" == bridge || "$3" == ac97 ) ]] || exit 2
original=$(realpath "$1")
image=$(realpath "$2")
[[ -f "$original" && -f "$image" && "$original" != "$image" && ! "$original" -ef "$image" ]] || exit 3
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
source "$(dirname -- "$0")/image_partition.sh"
mount_dir=$(mktemp -d /tmp/m90-audio-bridge.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
loop_device=$(image_loop_device "$image" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
mounted=1
python3 "$script_dir/audio_bridge_image.py" "$mount_dir" "$3"

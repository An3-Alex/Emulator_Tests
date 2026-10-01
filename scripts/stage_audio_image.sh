#!/usr/bin/env bash
# Mount only a distinct working copy, after the host's process guard.
set -euo pipefail
[[ $# -eq 6 ]] || exit 2
original=$(realpath "$1")
image=$(realpath "$2")
action=$3
[[ -f "$original" && -f "$image" && "$original" != "$image" && ! "$original" -ef "$image" ]] || {
  echo 'Original and working copy must be separate files' >&2; exit 3;
}
[[ $(stat -c %s "$image") == 16139354112 ]] || exit 3
case "$action" in check|install|verify|finish) ;; *) exit 2;; esac
script_dir=$(cd -- "$(dirname "$0")" && pwd)
mount_dir=$(mktemp -d /tmp/m90-audio-stage.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
if [[ "$action" == check ]]; then
  loop_device=$(losetup --find --show --read-only --offset 1048576 --sizelimit 16021151744 "$image")
  ntfs-3g -o ro "$loop_device" "$mount_dir"
else
  loop_device=$(losetup --find --show --offset 1048576 --sizelimit 16021151744 "$image")
  ntfs-3g -o big_writes "$loop_device" "$mount_dir"
fi
mounted=1
python3 "$script_dir/audio_image_stage.py" "$mount_dir" "$action" "$4" "$5" "$6"

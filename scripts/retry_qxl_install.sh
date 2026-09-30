#!/usr/bin/env bash
# Retry QXL SetupAPI installation only in a stopped, already staged working copy.
set -euo pipefail
[[ $# -eq 2 ]] || { echo 'usage: retry_qxl_install.sh WORKING_IMAGE QXL_INSTALLER' >&2; exit 2; }
image=$1
installer=$2
mount_dir=$(mktemp -d /tmp/m90-qxl-retry.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
[[ -f "$image" && $(stat -c %s "$image") == 16139354112 ]] || exit 3
[[ -f "$installer" && $(sha256sum "$installer" | cut -d' ' -f1) == \
  0e043b8fd7199d813596704be1c481b3c5643941af1a7a6cc15e15fcd379c296 ]] || exit 3
loop_device=$(losetup --find --show --read-only --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1
[[ $(cat "$mount_dir/NVRAM/m90_setup_stage.txt") == 'stage=qxl-verify' ]] || exit 3
[[ $(sha256sum "$mount_dir/WINDOWS/explorer.exe" | cut -d' ' -f1) == \
  aacd9215399d0122b46cb3b428dde15fad421de248e74e35c88b7de3645cc789 ]] || exit 3
umount "$mount_dir"
mounted=0
losetup -d "$loop_device"
loop_device=
loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
mounted=1
cp "$installer" "$mount_dir/WINDOWS/explorer.exe.new"
mv "$mount_dir/WINDOWS/explorer.exe.new" "$mount_dir/WINDOWS/explorer.exe"
printf 'stage=qxl-pnp\n' > "$mount_dir/NVRAM/m90_setup_stage.txt"
sync
echo 'QXL retry staged.'

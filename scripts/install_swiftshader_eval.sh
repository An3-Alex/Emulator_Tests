#!/usr/bin/env bash
set -euo pipefail
[[ $# == 2 ]] || exit 2
image=$(realpath "$1")
dll=$2
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$image" || exit 3
build3383=7c1934eb90b41ac4b6d0590abffef69ab01de5aa917f1630892174aeb537dd0c
build5003=fc5994b209a57a77275e5ecee1904cd9139a344c69e221e54f05af90580a90c9
expected=$(sha256sum "$dll" | cut -d' ' -f1)
[[ "$expected" == "$build3383" || "$expected" == "$build5003" ]] || exit 4
mount_dir=$(mktemp -d /tmp/m90-swift.XXXXXX)
loop_device=""
cleanup() {
  sync
  umount "$mount_dir" 2>/dev/null || true
  [[ -z "$loop_device" ]] || losetup -d "$loop_device"
  rmdir "$mount_dir"
}
trap cleanup EXIT
loop_device=$(losetup --find --show --offset 1048576 --sizelimit 16021151744 "$image")
ntfs-3g "$loop_device" "$mount_dir"
for dir in NVRAM WorkDir; do
  [[ -f "$mount_dir/$dir/game.exe" ]] || exit 5
  target="$mount_dir/$dir/d3d9.dll"
  [[ ! -e "$target" ]] || current=$(sha256sum "$target" | cut -d' ' -f1)
  [[ ! -e "$target" || "$current" == "$build3383" || "$current" == "$build5003" ]] || exit 6
done
for dir in NVRAM WorkDir; do
  cp "$dll" "$mount_dir/$dir/d3d9.dll"
  sha256sum "$mount_dir/$dir/d3d9.dll"
done

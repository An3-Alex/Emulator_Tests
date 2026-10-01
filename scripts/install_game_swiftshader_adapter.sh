#!/usr/bin/env bash
set -euo pipefail
[[ $# == 2 ]] || exit 2
image=$(realpath "$1")
patched=$2
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$image" || exit 3
before=13b38c44cde88ee4af9a01796814d3502505eb1eda1073f66899a35bbd32aba0
expected=24d826f34ef72f9e76634b67ca16aae954090a7109da338e249bfa8c972756e9
[[ $(sha256sum "$patched" | cut -d' ' -f1) == "$expected" ]] || exit 4
mount_dir=$(mktemp -d /tmp/m90-adapter.XXXXXX)
loop_device=""
cleanup() { sync; umount "$mount_dir" 2>/dev/null || true; [[ -z "$loop_device" ]] || losetup -d "$loop_device"; rmdir "$mount_dir"; }
trap cleanup EXIT
loop_device=$(losetup --find --show --offset 1048576 --sizelimit 16021151744 "$image")
ntfs-3g "$loop_device" "$mount_dir"
for dir in NVRAM WorkDir; do
  target="$mount_dir/$dir/game.exe"
  [[ $(sha256sum "$target" | cut -d' ' -f1) == "$before" ]] || exit 5
done
for dir in NVRAM WorkDir; do
  cp "$patched" "$mount_dir/$dir/game.exe.new"
  mv -f "$mount_dir/$dir/game.exe.new" "$mount_dir/$dir/game.exe"
  sha256sum "$mount_dir/$dir/game.exe"
done

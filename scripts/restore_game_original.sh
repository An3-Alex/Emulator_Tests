#!/usr/bin/env bash
set -euo pipefail

[[ $# == 1 ]] || { echo "usage: $0 IMAGE" >&2; exit 2; }
image=$(realpath "$1")
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$image" || exit 3
expected_original=27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d
expected_patched=c86068850a80bf2405a8f5724a5cb26ad0efd9ecd7d1a75076a15b651a73d221

mount_dir=$(mktemp -d /tmp/m90-restore-game.XXXXXX)
loop_device=""
cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  [[ -z "$loop_device" ]] || losetup -d "$loop_device" 2>/dev/null || true
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"

for directory in NVRAM WorkDir; do
  active="$mount_dir/$directory/game.exe"
  backup="$mount_dir/$directory/game_original.exe"
  active_hash=$(sha256sum "$active" | awk '{print $1}')
  backup_hash=$(sha256sum "$backup" | awk '{print $1}')
  [[ "$active_hash" == "$expected_patched" || "$active_hash" == "$expected_original" ]] || {
    echo "$directory active hash mismatch: $active_hash" >&2
    exit 4
  }
  [[ "$backup_hash" == "$expected_original" ]] || {
    echo "$directory backup hash mismatch: $backup_hash" >&2
    exit 5
  }
done

for directory in NVRAM WorkDir; do
  active="$mount_dir/$directory/game.exe"
  backup="$mount_dir/$directory/game_original.exe"
  cp -- "$backup" "$active.new"
  sync
  mv -f -- "$active.new" "$active"
  restored_hash=$(sha256sum "$active" | awk '{print $1}')
  [[ "$restored_hash" == "$expected_original" ]] || exit 6
  echo "$directory.restored=$restored_hash"
done

sync

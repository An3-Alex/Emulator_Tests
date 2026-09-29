#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE PATCHED_GAME" >&2
  exit 2
fi

image=$1
patched_game=$2
expected_original=27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d
expected_nullguard=7c75908848d95bde2f04797c82d496dff1301f637815a68c7d965ebdee81cb97
expected_patched=13b38c44cde88ee4af9a01796814d3502505eb1eda1073f66899a35bbd32aba0
mount_dir=$(mktemp -d /tmp/m90-game-audio.XXXXXX)
loop_device=""

cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device" 2>/dev/null || true; fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

patch_hash=$(sha256sum "$patched_game" | awk '{print $1}')
[[ "$patch_hash" == "$expected_patched" ]] || {
  echo "patched game hash mismatch: $patch_hash" >&2; exit 1;
}

loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"

for directory in NVRAM WorkDir; do
  active="$mount_dir/$directory/game.exe"
  original="$mount_dir/$directory/game_original.exe"
  active_hash=$(sha256sum "$active" | awk '{print $1}')
  original_hash=$(sha256sum "$original" | awk '{print $1}')
  [[ "$active_hash" == "$expected_nullguard" || "$active_hash" == "$expected_patched" ]] || {
    echo "$directory active game hash mismatch: $active_hash" >&2; exit 1;
  }
  [[ "$original_hash" == "$expected_original" ]] || {
    echo "$directory original backup hash mismatch: $original_hash" >&2; exit 1;
  }
  cp "$patched_game" "$active.new"
  mv -f "$active.new" "$active"
  echo "$directory.original=$original_hash"
  echo "$directory.active=$(sha256sum "$active" | awk '{print $1}')"
done

#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE GUEST_DIRECTORY" >&2
  exit 2
fi

image=$1
guest_directory=${2#/}
mount_dir=$(mktemp -d /tmp/m90-list.XXXXXX)
loop_device=""

cleanup() {
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then
    losetup -d "$loop_device" 2>/dev/null || true
  fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

loop_device=$(losetup --find --show --read-only \
  --offset 1048576 --sizelimit 16021151744 "$image")
ntfs-3g -o ro "$loop_device" "$mount_dir"
directory="$mount_dir/$guest_directory"
[[ -d "$directory" ]] || { echo "directory not found: $guest_directory" >&2; exit 1; }
find "$directory" -mindepth 1 -maxdepth 1 -printf '%f\t%y\t%s\n' | sort -f

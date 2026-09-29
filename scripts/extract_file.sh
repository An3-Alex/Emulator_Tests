#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 IMAGE GUEST_PATH OUTPUT" >&2
  exit 2
fi

image=$1
guest_path=${2#/}
output=$3
mount_dir=$(mktemp -d /tmp/m90-extract.XXXXXX)
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

source_file="$mount_dir/$guest_path"
if [[ ! -f "$source_file" ]]; then
  source_file=$(find "$mount_dir" -type f -ipath "$mount_dir/$guest_path" \
    -print -quit)
  if [[ -z "$source_file" ]]; then
    echo "guest file not found: $guest_path" >&2
    exit 1
  fi
fi
cp -- "$source_file" "$output"
echo "extracted=$output"

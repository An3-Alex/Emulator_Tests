#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 IMAGE GUEST_DIRECTORY OUTPUT_DIRECTORY" >&2
  exit 2
fi

image=$1
guest_directory=${2#/}
output_directory=$3
mount_dir=$(mktemp -d /tmp/m90-extract-dir.XXXXXX)
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

source_directory="$mount_dir/$guest_directory"
[[ -d "$source_directory" ]] || {
  echo "guest directory not found: $guest_directory" >&2
  exit 1
}
mkdir -p "$output_directory"
find "$source_directory" -maxdepth 1 -type f -exec cp -- {} "$output_directory"/ \;
echo "extracted_directory=$output_directory"


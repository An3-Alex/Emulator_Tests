#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 IMAGE REGEX [GLOB]" >&2
  exit 2
fi

image=$1
regex=$2
glob=${3:-'*'}
mount_dir=$(mktemp -d /tmp/m90-search.XXXXXX)
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

grep -IrilE --include="$glob" -- "$regex" "$mount_dir" || true

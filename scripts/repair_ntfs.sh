#!/usr/bin/env bash
set -euo pipefail

[[ $# == 1 ]] || { echo "usage: $0 IMAGE" >&2; exit 2; }
image=$(realpath "$1")
[[ "$image" == /mnt/c/Users/User/Desktop/m90_work.img ]] || exit 3
loop_device=""
cleanup() {
  [[ -z "$loop_device" ]] || losetup -d "$loop_device" 2>/dev/null || true
}
trap cleanup EXIT
loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfsfix "$loop_device"

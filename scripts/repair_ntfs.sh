#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

[[ $# == 1 ]] || { echo "usage: $0 IMAGE" >&2; exit 2; }
image=$(realpath "$1")
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$image" || exit 3
loop_device=""
cleanup() {
  [[ -z "$loop_device" ]] || losetup -d "$loop_device" 2>/dev/null || true
}
trap cleanup EXIT
loop_device=$(image_loop_device "$image" rw)
ntfsfix "$loop_device"

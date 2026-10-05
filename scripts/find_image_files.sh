#!/bin/bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

IMAGE_PATH=${1:?usage: find_image_files.sh IMAGE_PATH NAME_PATTERN}
NAME_PATTERN=${2:?usage: find_image_files.sh IMAGE_PATH NAME_PATTERN}
MOUNT_PATH=/mnt/m90_ro
LOOP_DEVICE=

cleanup() {
    umount "$MOUNT_PATH" 2>/dev/null || true
    if [ -n "$LOOP_DEVICE" ]; then
        losetup -d "$LOOP_DEVICE" 2>/dev/null || true
    fi
}
trap cleanup EXIT

mkdir -p "$MOUNT_PATH"
LOOP_DEVICE=$(image_loop_device "$IMAGE_PATH" ro)
ntfs-3g -o ro "$LOOP_DEVICE" "$MOUNT_PATH"
find "$MOUNT_PATH" -type f -iname "$NAME_PATTERN" -print

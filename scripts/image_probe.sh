#!/bin/bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

IMAGE_PATH=${1:?usage: image_probe.sh IMAGE_PATH}
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

stat -c '%n %s bytes' \
    "$MOUNT_PATH/WINDOWS/system32/Cgos.dll" \
    "$MOUNT_PATH/WINDOWS/system32/ADPInterface.dll"
sha256sum \
    "$MOUNT_PATH/WINDOWS/system32/Cgos.dll" \
    "$MOUNT_PATH/WINDOWS/system32/ADPInterface.dll"


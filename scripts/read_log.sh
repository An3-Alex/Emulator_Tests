#!/bin/bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

IMAGE_PATH=${1:?usage: read_log.sh IMAGE_PATH}
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

if [ -f "$MOUNT_PATH/NVRAM/cgos_shim.log" ]; then
    cat "$MOUNT_PATH/NVRAM/cgos_shim.log"
elif [ -f "$MOUNT_PATH/cgos_shim.log" ]; then
    cat "$MOUNT_PATH/cgos_shim.log"
else
    echo 'CGOS_SHIM_LOG_NOT_FOUND'
    find "$MOUNT_PATH" -maxdepth 2 -iname '*cgos*log*' -print
fi


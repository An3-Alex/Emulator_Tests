#!/bin/bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

IMAGE_PATH=${1:?usage: rollback_shim.sh IMAGE_PATH}
EXPECTED_ORIGINAL=480703586ea6f5bdc9ae3d8aa7bb47f03fa4d8234b48a3f2abc92356fb76a14e
MOUNT_PATH=/mnt/m90_rw
LOOP_DEVICE=

cleanup() {
    sync || true
    umount "$MOUNT_PATH" 2>/dev/null || true
    if [ -n "$LOOP_DEVICE" ]; then
        losetup -d "$LOOP_DEVICE" 2>/dev/null || true
    fi
}
trap cleanup EXIT

mkdir -p "$MOUNT_PATH"
LOOP_DEVICE=$(image_loop_device "$IMAGE_PATH" rw)
ntfs-3g -o big_writes "$LOOP_DEVICE" "$MOUNT_PATH"

SYSTEM32="$MOUNT_PATH/WINDOWS/system32"
ORIGINAL="$SYSTEM32/Cgos_original.dll"
ACTIVE="$SYSTEM32/Cgos.dll"
FOUND=$(sha256sum "$ORIGINAL" | awk '{print $1}')
if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
    echo "backup hash mismatch: $FOUND" >&2
    exit 1
fi
cp "$ORIGINAL" "$SYSTEM32/Cgos.dll.rollback"
sync
mv -f "$SYSTEM32/Cgos.dll.rollback" "$ACTIVE"
sync
echo "restored=$(sha256sum "$ACTIVE" | awk '{print $1}')"


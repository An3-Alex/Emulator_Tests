#!/bin/bash
set -euo pipefail

IMAGE_PATH=${1:?usage: rollback_loader_patch.sh IMAGE_PATH}
EXPECTED_ORIGINAL=2fb4233b541431a1b940ed5af6f11096b7fd5846316e3c4e55bd0e9a7b37a5c1
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
LOOP_DEVICE=$(losetup --find --show --offset 1048576 \
    --sizelimit 16021151744 "$IMAGE_PATH")
ntfs-3g -o big_writes "$LOOP_DEVICE" "$MOUNT_PATH"

ACTIVE="$MOUNT_PATH/WINDOWS/explorer.exe"
ORIGINAL="$MOUNT_PATH/WINDOWS/explorer_original.exe"
FOUND=$(sha256sum "$ORIGINAL" | awk '{print $1}')
if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
    echo "loader backup hash mismatch: $FOUND" >&2
    exit 1
fi
cp "$ORIGINAL" "$ACTIVE.rollback"
sync
mv -f "$ACTIVE.rollback" "$ACTIVE"
sync
echo "restored=$(sha256sum "$ACTIVE" | awk '{print $1}')"

#!/bin/bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

IMAGE_PATH=${1:?usage: rollback_game_patch.sh IMAGE_PATH}
EXPECTED_ORIGINAL=27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d
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

for DIRECTORY in NVRAM WorkDir; do
    ACTIVE="$MOUNT_PATH/$DIRECTORY/game.exe"
    ORIGINAL="$MOUNT_PATH/$DIRECTORY/game_original.exe"
    FOUND=$(sha256sum "$ORIGINAL" | awk '{print $1}')
    if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
        echo "$DIRECTORY backup hash mismatch: $FOUND" >&2
        exit 1
    fi
    cp "$ORIGINAL" "$ACTIVE.rollback"
    sync
    mv -f "$ACTIVE.rollback" "$ACTIVE"
    echo "$DIRECTORY.restored=$(sha256sum "$ACTIVE" | awk '{print $1}')"
done
sync

#!/bin/bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

IMAGE_PATH=${1:?usage: install_loader_patch.sh IMAGE_PATH PATCHED_LOADER}
PATCHED_LOADER=${2:?usage: install_loader_patch.sh IMAGE_PATH PATCHED_LOADER}
EXPECTED_ORIGINAL=2fb4233b541431a1b940ed5af6f11096b7fd5846316e3c4e55bd0e9a7b37a5c1
EXPECTED_PATCHED=d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
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

if [ ! -f "$IMAGE_PATH" ] || [ ! -f "$PATCHED_LOADER" ]; then
    echo 'image or patched loader does not exist' >&2
    exit 1
fi

PATCH_HASH=$(sha256sum "$PATCHED_LOADER" | awk '{print $1}')
if [ "$PATCH_HASH" != "$EXPECTED_PATCHED" ]; then
    echo "patched loader hash mismatch: $PATCH_HASH" >&2
    exit 1
fi

mkdir -p "$MOUNT_PATH"
LOOP_DEVICE=$(image_loop_device "$IMAGE_PATH" rw)
ntfs-3g -o big_writes "$LOOP_DEVICE" "$MOUNT_PATH"

ACTIVE="$MOUNT_PATH/WINDOWS/explorer.exe"
ORIGINAL="$MOUNT_PATH/WINDOWS/explorer_original.exe"

if [ -e "$ORIGINAL" ]; then
    FOUND=$(sha256sum "$ORIGINAL" | awk '{print $1}')
    if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
        echo "existing loader backup hash mismatch: $FOUND" >&2
        exit 1
    fi
else
    FOUND=$(sha256sum "$ACTIVE" | awk '{print $1}')
    if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
        echo "active explorer.exe is not the verified loader: $FOUND" >&2
        exit 1
    fi
    mv "$ACTIVE" "$ORIGINAL"
fi

cp "$PATCHED_LOADER" "$ACTIVE.new"
sync
mv -f "$ACTIVE.new" "$ACTIVE"
sync

echo "original=$(sha256sum "$ORIGINAL" | awk '{print $1}')"
echo "active=$(sha256sum "$ACTIVE" | awk '{print $1}')"

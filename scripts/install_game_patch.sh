#!/bin/bash
set -euo pipefail

IMAGE_PATH=${1:?usage: install_game_patch.sh IMAGE_PATH PATCHED_GAME}
PATCHED_GAME=${2:?usage: install_game_patch.sh IMAGE_PATH PATCHED_GAME}
EXPECTED_ORIGINAL=27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d
EXPECTED_PATCHED=7c75908848d95bde2f04797c82d496dff1301f637815a68c7d965ebdee81cb97
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

PATCH_HASH=$(sha256sum "$PATCHED_GAME" | awk '{print $1}')
if [ "$PATCH_HASH" != "$EXPECTED_PATCHED" ]; then
    echo "patched game hash mismatch: $PATCH_HASH" >&2
    exit 1
fi

mkdir -p "$MOUNT_PATH"
LOOP_DEVICE=$(losetup --find --show --offset 1048576 \
    --sizelimit 16021151744 "$IMAGE_PATH")
ntfs-3g -o big_writes "$LOOP_DEVICE" "$MOUNT_PATH"

for DIRECTORY in NVRAM WorkDir; do
    ACTIVE="$MOUNT_PATH/$DIRECTORY/game.exe"
    ORIGINAL="$MOUNT_PATH/$DIRECTORY/game_original.exe"
    if [ -e "$ORIGINAL" ]; then
        FOUND=$(sha256sum "$ORIGINAL" | awk '{print $1}')
        if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
            echo "$DIRECTORY backup hash mismatch: $FOUND" >&2
            exit 1
        fi
    else
        FOUND=$(sha256sum "$ACTIVE" | awk '{print $1}')
        if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
            echo "$DIRECTORY/game.exe is not the verified original: $FOUND" >&2
            exit 1
        fi
        mv "$ACTIVE" "$ORIGINAL"
    fi
    cp "$PATCHED_GAME" "$ACTIVE.new"
    sync
    mv -f "$ACTIVE.new" "$ACTIVE"
    echo "$DIRECTORY.original=$(sha256sum "$ORIGINAL" | awk '{print $1}')"
    echo "$DIRECTORY.active=$(sha256sum "$ACTIVE" | awk '{print $1}')"
done
sync

#!/bin/bash
set -euo pipefail

IMAGE_PATH=$(realpath "${1:?usage: install_shim.sh IMAGE_PATH SHIM_DLL}")
SHIM_PATH=${2:?usage: install_shim.sh IMAGE_PATH SHIM_DLL}
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$IMAGE_PATH" || exit 3
EXPECTED_ORIGINAL=480703586ea6f5bdc9ae3d8aa7bb47f03fa4d8234b48a3f2abc92356fb76a14e
EXPECTED_SHIM=16c16aabce7f775be87ea12cc0dbc64637428f663ed8ce693e4e02e499b14d51
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

if [ ! -f "$IMAGE_PATH" ] || [ ! -f "$SHIM_PATH" ]; then
    echo 'image or shim does not exist' >&2
    exit 1
fi

SHIM_HASH=$(sha256sum "$SHIM_PATH" | awk '{print $1}')
if [ "$SHIM_HASH" != "$EXPECTED_SHIM" ]; then
    echo "shim hash mismatch: $SHIM_HASH" >&2
    exit 1
fi

mkdir -p "$MOUNT_PATH"
LOOP_DEVICE=$(losetup --find --show --offset 1048576 \
    --sizelimit 16021151744 "$IMAGE_PATH")
ntfs-3g -o big_writes "$LOOP_DEVICE" "$MOUNT_PATH"

SYSTEM32="$MOUNT_PATH/WINDOWS/system32"
ORIGINAL="$SYSTEM32/Cgos_original.dll"
ACTIVE="$SYSTEM32/Cgos.dll"

if [ -e "$ORIGINAL" ]; then
    FOUND=$(sha256sum "$ORIGINAL" | awk '{print $1}')
    if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
        echo "existing backup hash mismatch: $FOUND" >&2
        exit 1
    fi
else
    FOUND=$(sha256sum "$ACTIVE" | awk '{print $1}')
    if [ "$FOUND" != "$EXPECTED_ORIGINAL" ]; then
        echo "active Cgos.dll is not the verified original: $FOUND" >&2
        exit 1
    fi
    mv "$ACTIVE" "$ORIGINAL"
fi

cp "$SHIM_PATH" "$SYSTEM32/Cgos.dll.new"
sync
mv -f "$SYSTEM32/Cgos.dll.new" "$ACTIVE"
sync

echo "original=$(sha256sum "$ORIGINAL" | awk '{print $1}')"
echo "active=$(sha256sum "$ACTIVE" | awk '{print $1}')"

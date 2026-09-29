#!/bin/bash
set -euo pipefail

IMAGE_PATH=${1:?usage: archive_log.sh IMAGE_PATH ARCHIVE_NAME}
ARCHIVE_NAME=${2:?usage: archive_log.sh IMAGE_PATH ARCHIVE_NAME}
MOUNT_PATH=/mnt/m90_rw
LOOP_DEVICE=

case "$ARCHIVE_NAME" in
    *[!A-Za-z0-9._-]*|'') echo 'invalid archive name' >&2; exit 1 ;;
esac

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

SOURCE="$MOUNT_PATH/NVRAM/cgos_shim.log"
TARGET="$MOUNT_PATH/NVRAM/$ARCHIVE_NAME"
if [ -e "$SOURCE" ]; then
    if [ -e "$TARGET" ]; then
        echo "archive already exists: $TARGET" >&2
        exit 1
    fi
    mv "$SOURCE" "$TARGET"
    sync
    echo "archived=$TARGET"
else
    echo 'no active log to archive'
fi


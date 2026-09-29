#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE DISPLAY_HELPER_EXE" >&2
  exit 2
fi

image=$1
helper=$2
expected_loader=d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
expected_helper=9b3829ddc954143d03a1ebf797f6e9593c0171dcd89857206f8a80644c3a6ffd
mount_dir=$(mktemp -d /tmp/m90-display-shell.XXXXXX)
loop_device=""

cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device" 2>/dev/null || true; fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

helper_hash=$(sha256sum "$helper" | awk '{print $1}')
[[ "$helper_hash" == "$expected_helper" ]] || {
  echo "display helper hash mismatch: $helper_hash" >&2; exit 1;
}
loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
active="$mount_dir/WINDOWS/explorer.exe"
backup="$mount_dir/WINDOWS/explorer_adp_before_qxl.exe"
active_hash=$(sha256sum "$active" | awk '{print $1}')
backup_hash=$(sha256sum "$backup" | awk '{print $1}')
[[ "$active_hash" == "$expected_loader" ]] || {
  echo "active shell is not the verified patched loader: $active_hash" >&2; exit 1;
}
[[ "$backup_hash" == "$expected_loader" ]] || {
  echo "ADP shell backup hash mismatch: $backup_hash" >&2; exit 1;
}
cp "$helper" "$active.new"
mv -f "$active.new" "$active"
sync
echo "backup=$backup_hash"
echo "active=$(sha256sum "$active" | awk '{print $1}')"

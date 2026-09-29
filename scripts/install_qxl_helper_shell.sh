#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE QXL_INSTALLER_EXE" >&2
  exit 2
fi

image=$1
installer=$2
expected_loader=d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
expected_installer=934bad76f335230bd8f80e20355fb867db18758ec0cd183ce9e7080ac56d6756
mount_dir=$(mktemp -d /tmp/m90-qxl-shell.XXXXXX)
loop_device=""

cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then
    losetup -d "$loop_device" 2>/dev/null || true
  fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

actual_installer=$(sha256sum "$installer" | awk '{print $1}')
[[ "$actual_installer" == "$expected_installer" ]] || {
  echo "installer hash mismatch: $actual_installer" >&2
  exit 1
}

loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
active="$mount_dir/WINDOWS/explorer.exe"
backup="$mount_dir/WINDOWS/explorer_adp_before_qxl.exe"

if [[ -f "$backup" ]]; then
  backup_hash=$(sha256sum "$backup" | awk '{print $1}')
  [[ "$backup_hash" == "$expected_loader" ]] || {
    echo "ADP shell backup hash mismatch: $backup_hash" >&2
    exit 1
  }
else
  active_hash=$(sha256sum "$active" | awk '{print $1}')
  [[ "$active_hash" == "$expected_loader" ]] || {
    echo "active shell is not the verified patched loader: $active_hash" >&2
    exit 1
  }
  cp --preserve=timestamps "$active" "$backup"
fi

cp "$installer" "$active.new"
mv -f "$active.new" "$active"
sync
echo "backup=$(sha256sum "$backup" | awk '{print $1}')"
echo "active=$(sha256sum "$active" | awk '{print $1}')"

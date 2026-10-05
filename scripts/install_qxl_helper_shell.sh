#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE QXL_INSTALLER_EXE" >&2
  exit 2
fi

image=$1
installer=$2
expected_loader=d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
expected_installer=96797f2c715a74197211a9cfc598ef9680f5bea4869e4f0fa7f1f25048142f9a
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

loop_device=$(image_loop_device "$image" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
active="$mount_dir/WINDOWS/explorer.exe"
backup="$mount_dir/WINDOWS/explorer_adp_before_qxl.exe"
active_hash=$(sha256sum "$active" | awk '{print $1}')
[[ "$active_hash" == "$expected_installer" ||
   "$active_hash" == 0e043b8fd7199d813596704be1c481b3c5643941af1a7a6cc15e15fcd379c296 ||
   "$active_hash" == "$expected_loader" ]] || {
  echo 'unexpected active XP shell; refusing replacement' >&2; exit 3;
}

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

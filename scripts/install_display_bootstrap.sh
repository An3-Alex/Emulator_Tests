#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE DISPLAY_BOOTSTRAP_EXE" >&2
  exit 2
fi

image=$1
bootstrap=$2
expected_loader=d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
expected_bootstrap=ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6
mount_dir=$(mktemp -d /tmp/m90-display-bootstrap.XXXXXX)
loop_device=""

cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device" 2>/dev/null || true; fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

bootstrap_hash=$(sha256sum "$bootstrap" | awk '{print $1}')
[[ "$bootstrap_hash" == "$expected_bootstrap" ]] || {
  echo "display bootstrap hash mismatch: $bootstrap_hash" >&2; exit 1;
}
loop_device=$(image_loop_device "$image" rw)
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
cp "$bootstrap" "$active.new"
mv -f "$active.new" "$active"
sync
echo "ADP.loader=$backup_hash"
echo "active.bootstrap=$(sha256sum "$active" | awk '{print $1}')"

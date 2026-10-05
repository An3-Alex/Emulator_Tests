#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 IMAGE" >&2
  exit 2
fi

image=$1
expected_loader=d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
expected_installer=934bad76f335230bd8f80e20355fb867db18758ec0cd183ce9e7080ac56d6756
legacy_installer=00492c156e6444d6fa5b6d2ad60d098f650ee483db0a2c3c0f52c3493009cdba
display_helper=9b3829ddc954143d03a1ebf797f6e9593c0171dcd89857206f8a80644c3a6ffd
legacy_display_helper=476808d0b9256a2c5e08c2e0171e9a6a5643521530d37856e6102c1c00bc73d3
display_bootstrap=ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6
hda_installer=28916bca20bfe7f9e43411835ec7192385968f705e7ea26cac7506c253fab766
mount_dir=$(mktemp -d /tmp/m90-adp-shell.XXXXXX)
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

loop_device=$(image_loop_device "$image" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
active="$mount_dir/WINDOWS/explorer.exe"
backup="$mount_dir/WINDOWS/explorer_adp_before_qxl.exe"
backup_hash=$(sha256sum "$backup" | awk '{print $1}')
[[ "$backup_hash" == "$expected_loader" ]] || {
  echo "ADP shell backup hash mismatch: $backup_hash" >&2
  exit 1
}
active_hash=$(sha256sum "$active" | awk '{print $1}')
if [[ "$active_hash" != "$expected_installer" && \
      "$active_hash" != "$legacy_installer" && \
      "$active_hash" != "$display_helper" && \
      "$active_hash" != "$legacy_display_helper" && \
      "$active_hash" != "$display_bootstrap" && \
      "$active_hash" != "$hda_installer" && \
      "$active_hash" != "$expected_loader" ]]; then
  echo "refusing to replace unexpected active shell: $active_hash" >&2
  exit 1
fi
cp "$backup" "$active.new"
mv -f "$active.new" "$active"
sync
echo "active=$(sha256sum "$active" | awk '{print $1}')"

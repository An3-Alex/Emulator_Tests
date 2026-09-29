#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 IMAGE PATCHED_INF HDA_INSTALLER_EXE" >&2
  exit 2
fi

image=$1
patched_inf=$2
installer=$3
expected_loader=d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
expected_helper=9b3829ddc954143d03a1ebf797f6e9593c0171dcd89857206f8a80644c3a6ffd
expected_inf=7faf17a8fb487b1983c726b94414537b11265c060419a52d0518bf7e89afd9d5
expected_installer=28916bca20bfe7f9e43411835ec7192385968f705e7ea26cac7506c253fab766
mount_dir=$(mktemp -d /tmp/m90-hda-shell.XXXXXX)
loop_device=""

cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device" 2>/dev/null || true; fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

actual_inf=$(sha256sum "$patched_inf" | awk '{print $1}')
actual_installer=$(sha256sum "$installer" | awk '{print $1}')
[[ "$actual_inf" == "$expected_inf" ]] || {
  echo "patched INF hash mismatch: $actual_inf" >&2; exit 1;
}
[[ "$actual_installer" == "$expected_installer" ]] || {
  echo "installer hash mismatch: $actual_installer" >&2; exit 1;
}

loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"

active="$mount_dir/WINDOWS/explorer.exe"
backup="$mount_dir/WINDOWS/explorer_adp_before_qxl.exe"
active_hash=$(sha256sum "$active" | awk '{print $1}')
backup_hash=$(sha256sum "$backup" | awk '{print $1}')
[[ "$active_hash" == "$expected_helper" ]] || {
  echo "active shell is not the passive display helper: $active_hash" >&2; exit 1;
}
[[ "$backup_hash" == "$expected_loader" ]] || {
  echo "ADP shell backup hash mismatch: $backup_hash" >&2; exit 1;
}

stage="$mount_dir/NVRAM/hda-driver"
mkdir -p "$stage"
cp "$patched_inf" "$stage/hdaudio-qemu.inf.new"
mv -f "$stage/hdaudio-qemu.inf.new" "$stage/hdaudio-qemu.inf"
for source in \
  "$mount_dir/WINDOWS/system32/drivers/Hdaudio.sys" \
  "$mount_dir/WINDOWS/system32/HdAProp.dll" \
  "$mount_dir/WINDOWS/system32/HdAudRes.dll" \
  "$mount_dir/WINDOWS/system32/HdAShCut.exe"; do
  [[ -f "$source" ]] || { echo "missing HDA driver component: $source" >&2; exit 1; }
  cp "$source" "$stage/$(basename "$source").new"
  mv -f "$stage/$(basename "$source").new" "$stage/$(basename "$source")"
done

cp "$installer" "$active.new"
mv -f "$active.new" "$active"
sync

echo "backup=$backup_hash"
echo "active=$(sha256sum "$active" | awk '{print $1}')"
echo "patched.inf=$(sha256sum "$stage/hdaudio-qemu.inf" | awk '{print $1}')"
for file in Hdaudio.sys HdAProp.dll HdAudRes.dll HdAShCut.exe; do
  echo "$file=$(sha256sum "$stage/$file" | awk '{print $1}')"
done

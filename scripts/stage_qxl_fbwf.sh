#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 IMAGE QXL_DRIVER_DIR FBWF_START" >&2
  exit 2
fi

image=$1
driver_dir=$2
new_start=$3
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mount_dir=$(mktemp -d /tmp/m90-qxl.XXXXXX)
loop_device=""

case "$new_start" in
  0) expected_start=4 ;;
  4) expected_start=0 ;;
  *) echo "FBWF_START must be 0 or 4" >&2; exit 2 ;;
esac

cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then
    losetup -d "$loop_device" 2>/dev/null || true
  fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

declare -A expected_hashes=(
  [qxl.inf]=2c2ce985936c87406313d68ba54b1c36f42aec97ee357d894e3238aecda776fa
  [qxl.sys]=42be54fe601af95abeb716c3ab00656e731917fa83fda72ecc6084f39a7ccceb
  [qxldd.dll]=4f3f81b1b8ba18282e179c6c5dad1c171b5ec54262e88d6a09f94375f7906640
)
for name in qxl.inf qxl.sys qxldd.dll; do
  file="$driver_dir/$name"
  [[ -f "$file" ]] || { echo "missing driver file: $file" >&2; exit 1; }
  actual=$(sha256sum "$file" | awk '{print $1}')
  [[ "$actual" == "${expected_hashes[$name]}" ]] || {
    echo "$name hash mismatch: $actual" >&2
    exit 1
  }
done

loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"

system_hive="$mount_dir/WINDOWS/system32/config/SYSTEM"
backup_hive="$mount_dir/WINDOWS/system32/config/SYSTEM.pre-qxl-pnp"
[[ -f "$system_hive" ]] || { echo "SYSTEM hive not found" >&2; exit 1; }
if [[ ! -f "$backup_hive" ]]; then
  cp --preserve=timestamps "$system_hive" "$backup_hive"
fi

stage_dir="$mount_dir/NVRAM/qxl-driver"
mkdir -p "$stage_dir"
cp "$driver_dir/qxl.inf" "$stage_dir/qxl.inf.new"
cp "$driver_dir/qxl.sys" "$stage_dir/qxl.sys.new"
cp "$driver_dir/qxldd.dll" "$stage_dir/qxldd.dll.new"
mv -f "$stage_dir/qxl.inf.new" "$stage_dir/qxl.inf"
mv -f "$stage_dir/qxl.sys.new" "$stage_dir/qxl.sys"
mv -f "$stage_dir/qxldd.dll.new" "$stage_dir/qxldd.dll"

cp "$driver_dir/qxl.inf" "$mount_dir/WINDOWS/INF/qxl-spice-xp.inf.new"
mv -f "$mount_dir/WINDOWS/INF/qxl-spice-xp.inf.new" \
  "$mount_dir/WINDOWS/INF/qxl-spice-xp.inf"
cp "$driver_dir/qxl.sys" "$mount_dir/WINDOWS/system32/drivers/qxl.sys.new"
mv -f "$mount_dir/WINDOWS/system32/drivers/qxl.sys.new" \
  "$mount_dir/WINDOWS/system32/drivers/qxl.sys"
cp "$driver_dir/qxldd.dll" "$mount_dir/WINDOWS/system32/qxldd.dll.new"
mv -f "$mount_dir/WINDOWS/system32/qxldd.dll.new" \
  "$mount_dir/WINDOWS/system32/qxldd.dll"

python3 "$script_dir/set_registry_dword.py" \
  "$system_hive" ControlSet001/Services/FBWF Start "$new_start" \
  --expect "$expected_start"
sync

echo "backup.SYSTEM=$(sha256sum "$backup_hive" | awk '{print $1}')"
echo "active.SYSTEM=$(sha256sum "$system_hive" | awk '{print $1}')"
echo "fbwf.Start=$new_start"
for name in qxl.inf qxl.sys qxldd.dll; do
  echo "$name=$(sha256sum "$stage_dir/$name" | awk '{print $1}')"
done

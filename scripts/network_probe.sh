#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 IMAGE" >&2
  exit 2
fi

image=$1
mount_dir=$(mktemp -d /tmp/m90-net.XXXXXX)
loop_device=""

cleanup() {
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then
    losetup -d "$loop_device" 2>/dev/null || true
  fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

loop_device=$(image_loop_device "$image" ro)
ntfs-3g -o ro "$loop_device" "$mount_dir"

echo "== candidate network drivers =="
find "$mount_dir/WINDOWS/system32/drivers" -maxdepth 1 -type f \
  \( -iname '*e1000*' -o -iname '*e1*.sys' -o -iname '*rtl*.sys' \
     -o -iname '*net*.sys' \) \
  -printf '%f %s bytes\n' | sort

echo "== SYSTEM hive strings =="
system_hive=$(find "$mount_dir/WINDOWS/system32/config" -maxdepth 1 -type f \
  -iname system -print -quit)
strings -el "$system_hive" \
  | grep -Eai 'e1000|E100B|VEN_8086&DEV_1229|00408086|rtl8139|rtl816|rtl811|NetworkAddress|001395|00:13:95' \
  | sort -u | head -n 160 || true

echo "== matching PCI IDs in installed INF files =="
inf_dir=$(find "$mount_dir/WINDOWS" -maxdepth 1 -type d -iname inf -print -quit)
grep -Eail 'VEN_8086&DEV_100E|VEN_8086&DEV_1209|VEN_8086&DEV_1229|VEN_10EC&DEV_8139' \
  "$inf_dir"/*.inf | while IFS= read -r inf; do
    echo "-- $(basename "$inf")"
    grep -Eai 'VEN_8086&DEV_100E|VEN_8086&DEV_1209|VEN_8086&DEV_1229|VEN_10EC&DEV_8139' \
      "$inf" | head -n 20
  done || true

echo "== loader strings (ASCII and UTF-16LE) =="
for mode in -a -el; do
  strings "$mode" "$mount_dir/WINDOWS/explorer.exe" \
    | grep -Eai 'MAC|ZERO|HARDWARE ERROR|Platform|B945' \
    | head -n 160 || true
done

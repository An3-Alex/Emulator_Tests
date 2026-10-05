#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

[[ $# == 2 ]] || { echo "usage: $0 IMAGE force|default" >&2; exit 2; }
image=$(realpath "$1")
mode=$2
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$image" || exit 3
default='autocheck autochk *'
forced='autocheck autochk /p \??\C:'
case "$mode" in
  force) expected=$default; replacement=$forced ;;
  default) expected=$forced; replacement=$default ;;
  *) exit 4 ;;
esac
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mount_dir=$(mktemp -d /tmp/m90-autochk.XXXXXX)
loop_device=""
cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  [[ -z "$loop_device" ]] || losetup -d "$loop_device" 2>/dev/null || true
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT
loop_device=$(image_loop_device "$image" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
hive="$mount_dir/WINDOWS/system32/config/SYSTEM"
backup="$mount_dir/WINDOWS/system32/config/SYSTEM.pre-codex-autochk"
[[ -f "$hive" ]] || exit 5
if [[ "$mode" == force && ! -e "$backup" ]]; then
  cp --preserve=timestamps "$hive" "$backup"
fi
python3 "$script_dir/set_registry_multisz.py" "$hive" \
  'ControlSet001/Control/Session Manager' BootExecute "$replacement" \
  --expect "$expected"
sync
echo "mode=$mode"
echo "SYSTEM=$(sha256sum "$hive" | awk '{print $1}')"

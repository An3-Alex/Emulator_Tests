#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE FBWFLIB_PROXY" >&2
  exit 2
fi

image=$(realpath "$1")
proxy=$2
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$image" || exit 3
expected_proxy=31cac0b2141d2c8896e9dcf5cc2bd19ea0e3e0e1196625b9a79645d1edc6c9d6
legacy_proxy_service=4d62ee6e183ba534f7ac7d2780d4a5fb90f2394bc4fc68fd6d6b9ea640b3aa94
legacy_proxy_runtime=cebb001fcc05beeaa78efec80de3600658399b79f54084269d48f44934302149
legacy_proxy_v4=c504ce2519f330f12eed2f58e727823dac04c6e82d7c783c45d27a785072469d
legacy_proxy=d6b8d03d19384d5461aedcf09f37910ae9671d9f9377837bab92940f004d1124
legacy_proxy_v1=e639963de15cb73dba1f84fb31e7361d28aa53824ea139b95986f76821fc95a2
legacy_proxy_v0=bb88c44e2ece86a747bb604e7fa0518f1bb08ca49d328f4586dbe0ed53bedd8e
expected_game=27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d
expected_system_fbwf=17dc9581c25b9c77d2d4368c3923f83e414d6be9a8acf675871ff2ce6df28263
mount_dir=$(mktemp -d /tmp/m90-sram.XXXXXX)
loop_device=""

cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device" 2>/dev/null || true; fi
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

proxy_hash=$(sha256sum "$proxy" | awk '{print $1}')
[[ "$proxy_hash" == "$expected_proxy" ]] || {
  echo "SRAM proxy hash mismatch: $proxy_hash" >&2
  exit 1
}

loop_device=$(image_loop_device "$image" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"

system_fbwf="$mount_dir/WINDOWS/system32/fbwflib.dll"
system_hash=$(sha256sum "$system_fbwf" | awk '{print $1}')
[[ "$system_hash" == "$expected_system_fbwf" ]] || {
  echo "system FBWFLIB.dll hash mismatch: $system_hash" >&2
  exit 1
}

for directory in NVRAM WorkDir; do
  game="$mount_dir/$directory/game.exe"
  game_hash=$(sha256sum "$game" | awk '{print $1}')
  [[ "$game_hash" == "$expected_game" ]] || {
    echo "$directory/game.exe hash mismatch: $game_hash" >&2
    exit 1
  }
  target="$mount_dir/$directory/FBWFLIB.dll"
  if [[ -f "$target" ]]; then
    current_hash=$(sha256sum "$target" | awk '{print $1}')
    [[ "$current_hash" == "$expected_proxy" || "$current_hash" == "$legacy_proxy_service" || \
       "$current_hash" == "$legacy_proxy_runtime" || \
       "$current_hash" == "$legacy_proxy_v4" || \
       "$current_hash" == "$legacy_proxy" || \
       "$current_hash" == "$legacy_proxy_v1" || \
       "$current_hash" == "$legacy_proxy_v0" ]] || {
      echo "refusing to overwrite unexpected $directory/FBWFLIB.dll: $current_hash" >&2
      exit 1
    }
  fi
  cp "$proxy" "$target.new"
  mv -f "$target.new" "$target"
  echo "$directory/FBWFLIB.dll=$(sha256sum "$target" | awk '{print $1}')"
done
python3 "$(dirname -- "$0")/service_sram.py" "$mount_dir" "$proxy"

if [[ -f "$mount_dir/NVRAM/sram_compat.log" ]]; then
  mv "$mount_dir/NVRAM/sram_compat.log" \
     "$mount_dir/NVRAM/sram_compat.pre-install.log"
fi
sync
echo "system.FBWFLIB.dll=$system_hash"

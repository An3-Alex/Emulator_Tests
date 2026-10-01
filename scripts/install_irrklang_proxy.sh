#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

[[ $# == 2 ]] || { echo "usage: $0 IMAGE PROXY_DLL" >&2; exit 2; }
image=$(realpath "$1")
proxy=$2
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$image" || exit 3
expected_proxy=0e811b9ddeedb53d9494e5ac3ca871743ad51a0c9ecf654cf76102a9af83b10d
legacy_proxy_runtime=5bb36e7dae437afb3e68895237d18b65d50f5fad2dbe4a2c905b8fc6006af361
original_irrklang=ab0bff115cf3f55a608a7059ae3fd5fdc73a5f4ee814db4aa4b6c7e44cce8297
expected_original_game=27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d

[[ $(sha256sum "$proxy" | awk '{print $1}') == "$expected_proxy" ]] || exit 4

mount_dir=$(mktemp -d /tmp/m90-irrproxy.XXXXXX)
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

for directory in NVRAM WorkDir; do
  game_hash=$(sha256sum "$mount_dir/$directory/game.exe" | awk '{print $1}')
  [[ "$game_hash" == "$expected_original_game" ]] || {
    echo "$directory/game.exe is not the verified original: $game_hash" >&2
    exit 5
  }
done

for directory in NVRAM WorkDir; do
  target="$mount_dir/$directory/irrKlang.dll"
  if [[ -f "$target" ]]; then
    current_hash=$(sha256sum "$target" | awk '{print $1}')
    [[ "$current_hash" == "$expected_proxy" || \
       "$current_hash" == "$legacy_proxy_runtime" || \
       "$current_hash" == "$original_irrklang" ]] || {
      echo "refusing unexpected $directory/irrKlang.dll: $current_hash" >&2
      exit 6
    }
  fi
  cp -- "$proxy" "$target.new"
  sync
  mv -f -- "$target.new" "$target"
  echo "$directory.irrKlang=$(sha256sum "$target" | awk '{print $1}')"
done

sync

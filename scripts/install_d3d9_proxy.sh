#!/usr/bin/env bash
set -euo pipefail

[[ $# == 2 ]] || { echo "usage: $0 IMAGE D3D9_PROXY" >&2; exit 2; }
image=$(realpath "$1")
proxy=$2
source "$(dirname -- "$0")/working_image_guard.sh"
require_working_image "$image" || exit 3
expected_proxy=31d2d484d4821ef34dd764e68a66338ed638926c66b73b14078d360713f4987f
legacy_proxy_v1=801eb42c6af73542ecb290f4844bf6ddab5a9a5daf2c3a963a66583188fd06d3
swiftshader5003=fc5994b209a57a77275e5ecee1904cd9139a344c69e221e54f05af90580a90c9
expected_game=27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d

[[ $(sha256sum "$proxy" | awk '{print $1}') == "$expected_proxy" ]] || {
  echo "D3D9 proxy hash mismatch" >&2; exit 4;
}

mount_dir=$(mktemp -d /tmp/m90-d3d9-proxy.XXXXXX)
loop_device=""
cleanup() {
  sync || true
  umount "$mount_dir" 2>/dev/null || true
  [[ -z "$loop_device" ]] || losetup -d "$loop_device" 2>/dev/null || true
  rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT

loop_device=$(losetup --find --show --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o big_writes "$loop_device" "$mount_dir"

for directory in NVRAM WorkDir; do
  game="$mount_dir/$directory/game.exe"
  [[ $(sha256sum "$game" | awk '{print $1}') == "$expected_game" ]] || {
    echo "$directory/game.exe is not the verified original" >&2; exit 5;
  }
  active="$mount_dir/$directory/d3d9.dll"
  real="$mount_dir/$directory/swiftshader_d3d9.dll"
  active_hash=$(sha256sum "$active" | awk '{print $1}')
  if [[ -f "$real" ]]; then
    [[ $(sha256sum "$real" | awk '{print $1}') == "$swiftshader5003" ]] || {
      echo "unexpected $directory/swiftshader_d3d9.dll" >&2; exit 6;
    }
    [[ "$active_hash" == "$expected_proxy" || "$active_hash" == "$legacy_proxy_v1" || \
       "$active_hash" == "$swiftshader5003" ]] || {
      echo "unexpected $directory/d3d9.dll: $active_hash" >&2; exit 7;
    }
  else
    [[ "$active_hash" == "$swiftshader5003" ]] || {
      echo "$directory/d3d9.dll is not verified SwiftShader 5003: $active_hash" >&2; exit 8;
    }
    cp -- "$active" "$real.new"
    sync
    mv -f -- "$real.new" "$real"
  fi
done

for directory in NVRAM WorkDir; do
  target="$mount_dir/$directory/d3d9.dll"
  cp -- "$proxy" "$target.new"
  sync
  mv -f -- "$target.new" "$target"
  rm -f -- "$mount_dir/$directory/d3d9_proxy.log"
  echo "$directory/d3d9=$(sha256sum "$target" | awk '{print $1}')"
  echo "$directory/swiftshader=$(sha256sum "$mount_dir/$directory/swiftshader_d3d9.dll" | awk '{print $1}')"
done
rm -f -- "$mount_dir/NVRAM/d3d9_proxy.log"
sync

#!/usr/bin/env bash
# First, offline phase only. Never mounts or writes the owner's source image RW.
set -euo pipefail
source "$(dirname -- "$0")/image_partition.sh"

if [[ $# -ne 10 ]]; then
  echo 'usage: prepare_image_stage.sh ORIGINAL OUTPUT CGOS BOOTSTRAP QXL_INSTALLER D3D9 FBWFLIB IRRKLANG SWIFTSHADER QXL_DIR' >&2
  exit 2
fi
source_image=$1
output_image=$2
shim=$3
bootstrap=$4
qxl_installer=$5
d3d9=$6
fbwf=$7
irrklang=$8
swiftshader=$9
qxl_dir=${10}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
partial_image="${output_image}.m90-partial"
mount_dir=$(mktemp -d /tmp/m90-prepare.XXXXXX)
loop_device=
mounted=0

cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT

verify_hash() {
  local file=$1 expected=$2 actual
  [[ -f "$file" ]] || { echo "missing file: $file" >&2; exit 3; }
  actual=$(sha256sum "$file" | cut -d' ' -f1)
  [[ "$actual" == "$expected" ]] || {
    echo "hash mismatch: $file ($actual)" >&2; exit 3;
  }
}

[[ -f "$source_image" ]] || { echo 'source image missing' >&2; exit 3; }
[[ -d $(dirname -- "$output_image") ]] || { echo 'output directory missing' >&2; exit 3; }
[[ $(realpath -m "$source_image") != $(realpath -m "$output_image") ]] || {
  echo 'source and output must differ' >&2; exit 3;
}
[[ ! -e "$output_image" && ! -e "$partial_image" ]] || {
  echo 'output or partial image already exists; refusing overwrite' >&2; exit 3;
}
verify_hash "$shim" 16c16aabce7f775be87ea12cc0dbc64637428f663ed8ce693e4e02e499b14d51
verify_hash "$bootstrap" fcc3019fb0c890a6e252985ea2ca413110c527b256b6cb4d8360e97797fbc0ea
verify_hash "$qxl_installer" 96797f2c715a74197211a9cfc598ef9680f5bea4869e4f0fa7f1f25048142f9a
verify_hash "$d3d9" cc152b096bf74a01bfd23f0dece9e8f619eb8dfcc38c405faebce6cb19d20737
verify_hash "$fbwf" 4d62ee6e183ba534f7ac7d2780d4a5fb90f2394bc4fc68fd6d6b9ea640b3aa94
verify_hash "$irrklang" 11db62d22889c3ac8368f464e24463b0653bb23be8d116b78cc73fc8f30c6ba7
verify_hash "$swiftshader" fc5994b209a57a77275e5ecee1904cd9139a344c69e221e54f05af90580a90c9
verify_hash "$qxl_dir/qxl.inf" 2c2ce985936c87406313d68ba54b1c36f42aec97ee357d894e3238aecda776fa
verify_hash "$qxl_dir/qxl.sys" 42be54fe601af95abeb716c3ab00656e731917fa83fda72ecc6084f39a7ccceb
verify_hash "$qxl_dir/qxldd.dll" 4f3f81b1b8ba18282e179c6c5dad1c171b5ec54262e88d6a09f94375f7906640

mount_image() {
  local image=$1 mode=$2
  if [[ "$mode" == ro ]]; then
    loop_device=$(image_loop_device "$image" ro)
    ntfs-3g -o ro "$loop_device" "$mount_dir"
  else
    loop_device=$(image_loop_device "$image" rw)
    ntfs-3g -o big_writes "$loop_device" "$mount_dir"
  fi
  mounted=1
}
unmount_image() {
  sync
  umount "$mount_dir"
  mounted=0
  losetup -d "$loop_device"
  loop_device=
}

verify_original_guest() {
  verify_hash "$mount_dir/WINDOWS/system32/Cgos.dll" 480703586ea6f5bdc9ae3d8aa7bb47f03fa4d8234b48a3f2abc92356fb76a14e
  verify_hash "$mount_dir/WINDOWS/explorer.exe" 2fb4233b541431a1b940ed5af6f11096b7fd5846316e3c4e55bd0e9a7b37a5c1
  verify_hash "$mount_dir/WINDOWS/system32/fbwflib.dll" 17dc9581c25b9c77d2d4368c3923f83e414d6be9a8acf675871ff2ce6df28263
  [[ -f "$mount_dir/WINDOWS/system32/config/SYSTEM" ]] || {
    echo 'XP SYSTEM hive missing' >&2; exit 3;
  }
  for dir in NVRAM WorkDir; do
    verify_hash "$mount_dir/$dir/game.exe" 27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d
    [[ ! -e "$mount_dir/$dir/d3d9.dll" ]] || {
      echo "source already has $dir/d3d9.dll" >&2; exit 3;
    }
  done
}

mount_image "$source_image" ro
verify_original_guest
unmount_image

available=$(df -B1 --output=avail "$(dirname -- "$output_image")" | tail -n 1 | tr -d ' ')
source_bytes=$(stat -c %s "$source_image")
required_bytes=$((source_bytes + 268435456))
[[ "$available" =~ ^[0-9]+$ && "$available" -ge "$required_bytes" ]] || {
  echo "working copy needs $required_bytes bytes of free space" >&2; exit 3;
}
echo 'Creating separate working copy; source stays read-only.'
cp --reflink=auto --sparse=always -- "$source_image" "$partial_image"
[[ $(stat -c %s "$partial_image") == "$source_bytes" ]] || {
  echo "incomplete copy left at $partial_image" >&2; exit 3;
}
mount_image "$partial_image" rw
verify_original_guest

cp --preserve=timestamps "$mount_dir/WINDOWS/system32/Cgos.dll" \
  "$mount_dir/WINDOWS/system32/Cgos_original.dll"
cp "$shim" "$mount_dir/WINDOWS/system32/Cgos.dll.new"
mv "$mount_dir/WINDOWS/system32/Cgos.dll.new" "$mount_dir/WINDOWS/system32/Cgos.dll"
cp --preserve=timestamps "$mount_dir/WINDOWS/explorer.exe" \
  "$mount_dir/WINDOWS/explorer_original.exe"
python3 "$script_dir/patch_loader_null_device.py" \
  "$mount_dir/WINDOWS/explorer_original.exe" \
  "$mount_dir/WINDOWS/explorer_adp_before_qxl.exe"
verify_hash "$mount_dir/WINDOWS/explorer_adp_before_qxl.exe" d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb
cp "$qxl_installer" "$mount_dir/WINDOWS/explorer.exe.new"
mv "$mount_dir/WINDOWS/explorer.exe.new" "$mount_dir/WINDOWS/explorer.exe"
cp --preserve=timestamps "$mount_dir/WINDOWS/system32/config/SYSTEM" \
  "$mount_dir/WINDOWS/system32/config/SYSTEM.pre-qxl-pnp"

mkdir -p "$mount_dir/NVRAM/qxl-driver"
for name in qxl.inf qxl.sys qxldd.dll; do
  cp "$qxl_dir/$name" "$mount_dir/NVRAM/qxl-driver/$name"
done
cp "$qxl_dir/qxl.inf" "$mount_dir/WINDOWS/INF/qxl-spice-xp.inf"
cp "$qxl_dir/qxl.sys" "$mount_dir/WINDOWS/system32/drivers/qxl.sys"
cp "$qxl_dir/qxldd.dll" "$mount_dir/WINDOWS/system32/qxldd.dll"
for dir in NVRAM WorkDir; do
  cp "$d3d9" "$mount_dir/$dir/d3d9.dll"
  cp "$fbwf" "$mount_dir/$dir/FBWFLIB.dll"
  cp "$irrklang" "$mount_dir/$dir/irrKlang.dll"
  cp "$swiftshader" "$mount_dir/$dir/swiftshader_d3d9.dll"
done
python3 "$script_dir/set_registry_dword.py" \
  "$mount_dir/WINDOWS/system32/config/SYSTEM" ControlSet001/Services/FBWF Start 4 --expect 0
printf 'stage=qxl-pnp\n' > "$mount_dir/NVRAM/m90_setup_stage.txt"
unmount_image
mv -- "$partial_image" "$output_image"
echo "Staged working copy: $output_image"
echo 'Next: boot QXL setup guest, verify two installed devices, then finalize.'

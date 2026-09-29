#!/usr/bin/env bash
# Read only; reports an image's preparation marker without changing its contents.
set -euo pipefail
[[ $# -eq 1 ]] || { echo 'usage: check_image_stage.sh IMAGE' >&2; exit 2; }
image=$1
[[ -f "$image" && $(stat -c %s "$image") == 16139354112 ]] || exit 3
mount_dir=$(mktemp -d /tmp/m90-stage-check.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
loop_device=$(losetup --find --show --read-only --offset 1048576 \
  --sizelimit 16021151744 "$image")
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1
marker="$mount_dir/NVRAM/m90_setup_stage.txt"
if [[ -f "$marker" ]]; then
  value=$(cat "$marker")
  case "$value" in
    stage=qxl-pnp) echo qxl-pnp ;;
    stage=ready) echo ready ;;
    *) echo unknown ;;
  esac
else
  cgos="$mount_dir/WINDOWS/system32/Cgos.dll"
  shell="$mount_dir/WINDOWS/explorer.exe"
  if [[ -f "$cgos" && -f "$shell" ]]; then
    cgos_hash=$(sha256sum "$cgos" | cut -d' ' -f1)
    shell_hash=$(sha256sum "$shell" | cut -d' ' -f1)
    if [[ "$cgos_hash" == 16c16aabce7f775be87ea12cc0dbc64637428f663ed8ce693e4e02e499b14d51 && \
          "$shell_hash" == ebee642da544bbd038cdbddaacf15b20c88a563115a90da65b7baf6dc6693bd6 ]]; then
      echo legacy-ready
    else
      echo unprepared
    fi
  else
    echo unknown
  fi
fi

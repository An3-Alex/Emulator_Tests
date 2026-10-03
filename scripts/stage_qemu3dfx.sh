#!/usr/bin/env bash
# Offline installation into an explicitly selected prepared copy; no VM boot.
set -euo pipefail
[[ $# == 4 ]] || { echo 'usage: stage_qemu3dfx.sh SOURCE_IMAGE GPU_WORKING_IMAGE BUNDLE WINDOWS_WORKING_PATH' >&2; exit 2; }
source "$(dirname -- "$0")/image_partition.sh"
source_image=$(realpath -e -- "$1")
image=$(realpath -e -- "$2")
bundle=$(realpath -e -- "$3")
[[ -f "$source_image" && -f "$image" && "$source_image" != "$image" && ! "$source_image" -ef "$image" ]] || {
  echo 'Source and GPU working image must be different existing files' >&2; exit 3;
}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
python3 "$script_dir/qemu3dfx_package.py" validate "$bundle"
mount_dir=$(mktemp -d /tmp/m90-qemu3dfx.XXXXXX)
loop_device=
mounted=0
cleanup() {
  if (( mounted )); then sync; umount "$mount_dir"; fi
  if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
  rmdir "$mount_dir"
}
trap cleanup EXIT
loop_device=$(image_loop_device "$image" ro)
ntfs-3g -o ro "$loop_device" "$mount_dir"
mounted=1
python3 "$script_dir/qemu3dfx_image.py" "$mount_dir" "$bundle" --check-only
umount "$mount_dir"; mounted=0
losetup -d "$loop_device"; loop_device=
loop_device=$(image_loop_device "$image" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
mounted=1
python3 "$script_dir/qemu3dfx_image.py" "$mount_dir" "$bundle"
sync
umount "$mount_dir"; mounted=0
losetup -d "$loop_device"; loop_device=
# Emit the Windows identity only after the updated image has been unmounted.
python3 - "$image" "$bundle" "$4" <<'PY'
import hashlib, json, pathlib, sys
image, bundle = map(pathlib.Path, sys.argv[1:3])
receipt = pathlib.Path(str(image) + '.qemu3dfx.json')
if receipt.is_symlink(): raise SystemExit('Symlink receipt refused')
value = dict(version=1, backend='qemu3dfx-hybrid', image=sys.argv[3],
             bundle_sha256=hashlib.sha256((bundle/'manifest.json').read_bytes()).hexdigest())
temporary = receipt.with_suffix(receipt.suffix + '.new')
if temporary.exists() or temporary.is_symlink(): raise SystemExit('Unfinished receipt')
temporary.write_text(json.dumps(value, indent=2) + '\n')
temporary.replace(receipt)
PY

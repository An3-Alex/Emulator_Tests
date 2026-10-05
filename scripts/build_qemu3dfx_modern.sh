#!/usr/bin/env bash
# Build the Windows host only; no guest or host driver is started.
set -euo pipefail
[[ $# == 2 && ${MSYSTEM:-} == UCRT64 ]] || { echo 'Use UCRT64: build_qemu3dfx_modern.sh HOST_CHECKOUT GPU_CHECKOUT' >&2; exit 2; }
src=$(realpath -e -- "$1")
gpu=$(realpath -e -- "$2")
python3 "$(dirname -- "$0")/qemu3dfx_modern_port.py" "$src" "$gpu"
mkdir -p "$src/build-m90"
cd "$src/build-m90"
src_win=$(cygpath -m "$src")
flags="-ffile-prefix-map=$src=/usr/src/qemu -ffile-prefix-map=$src_win=/usr/src/qemu"
"$src/configure" --target-list=x86_64-softmmu --enable-whpx --enable-sdl \
  --enable-opengl --enable-slirp --disable-gtk --disable-werror --disable-docs \
  --extra-cflags="$flags" --extra-cxxflags="$flags"
jobs=${M90_BUILD_JOBS:-2}
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid M90_BUILD_JOBS' >&2; exit 3; }
ninja -j"$jobs" qemu-system-x86_64.exe
printf 'QEMU3DFX_MODERN_HOST_COMPLETE (no guest started)\n'

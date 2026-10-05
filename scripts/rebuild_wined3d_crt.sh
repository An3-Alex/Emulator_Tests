#!/usr/bin/env bash
# Relink an already built, pinned Wine 1.8.7 checkout; no toolchain mutation.
set -eo pipefail
[[ $# == 1 && ${MSYSTEM:-} == MINGW32 ]] || exit 2
checkout=$(realpath -e -- "$1")
[[ $(git -C "$checkout" rev-parse HEAD) == f977ef3903b444fb5cfd65c543cde1ad5e8f601c ]] || exit 3
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
/ucrt64/bin/python.exe "$script_dir/wined3d_crt_patch.py" "$checkout"
# Load only upstream function definitions; do not run its global toolchain setup.
source <(sed '/^main "\$@"$/d' "$checkout/build-ci.sh")
SCRIPT_DIR="$checkout"
WINE9X="$checkout/wine9x-support"
build="$checkout/build/1.8.7"
for module in wined3d d3d9; do
  [[ -f "$build/$module/_heap_compat.o" && -f "$build/$module.def" ]] || exit 3
  gcc -std=c99 -O3 -fomit-frame-pointer -march=pentium2 -mtune=core2 \
    -fno-builtin -fno-tree-loop-distribute-patterns \
    -c -o "$build/$module/_heap_compat.o" "$checkout/docker/heap_compat.c"
  gcc -std=c99 -O3 -fomit-frame-pointer -march=pentium2 -mtune=core2 \
    -fno-builtin -fno-tree-loop-distribute-patterns \
    -c -o "$build/$module/_mingw_matherr_stubs.o" "$checkout/docker/mingw_matherr_stubs.c"
done
link_wined3d "$build/wined3d" "$build/wined3d.dll" "$build/wined3d.def"
link_d3d d3d9 "$build/d3d9" "$build/d3d9.dll" "$build/d3d9.def" "$build"
mkdir -p "$checkout/output/1.8.7"
cp "$build/wined3d.dll" "$build/d3d9.dll" "$checkout/output/1.8.7/"
/ucrt64/bin/python.exe "$checkout/docker/strip_import_decorations.py" "$checkout/output/1.8.7"
printf 'WINED3D_CRT_REBUILT\n'

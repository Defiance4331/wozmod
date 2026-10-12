#!/bin/bash
# Build wozmod-<version>-windows.zip on Linux: the Windows program runs under Wine, with no GitHub Actions.
#
#   packaging/build_windows.sh ASSETS_DIR
#
# ASSETS_DIR holds the release d3d9.dll and lettering.zip (as for packaging/build.py --assets).
# The zip lands in build/packaging/. Needs: wine (64-bit), uv, rustup with a stable toolchain, curl,
# and MinGW (Fedora: sudo dnf install mingw64-gcc mingw64-binutils mingw64-winpthreads-static).
# Everything it sets up lives in WOZMOD_WIN_WORK (default work/windows-build) and is reused next time:
# a Wine prefix, Windows CPython (with Tcl/Tk), the locked Python packages and the cross-built Rust codecs.
# Downloads are pinned: Python below, ffmpeg in packaging/ffmpeg.json, packages in uv.lock (with hashes).
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ASSETS=$(cd "${1:?usage: packaging/build_windows.sh ASSETS_DIR}" && pwd)
WORK=${WOZMOD_WIN_WORK:-$ROOT/work/windows-build}
PYTHON_VERSION=3.14.8   # keep equal to the CPython that uv uses (.python-version is 3.14)
PYTHON_URL=https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe
PYTHON_SHA256=759be887b96e736a3ca886daf8d575f18fcae1a09efab6902f42d59e8999f8ef
MATURIN=maturin==1.15.0

for tool in wine uv curl x86_64-w64-mingw32-gcc x86_64-w64-mingw32-dlltool; do
  command -v "$tool" >/dev/null || { echo "missing $tool (see the header of this script)" >&2; exit 1; }
done
[ -x "$HOME/.cargo/bin/rustup" ] || { echo "missing rustup in ~/.cargo/bin" >&2; exit 1; }

export WINEPREFIX=$WORK/prefix WINEARCH=win64 WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
winpath() { winepath -w "$1" 2>/dev/null; }
PY=$WORK/prefix/drive_c/wozmod-python/python.exe
mkdir -p "$WORK/dl" "$ROOT/build/packaging/cache"

fetch() {  # fetch URL SHA256 FILE
  if [ ! -f "$3" ] || [ "$(sha256sum "$3" | cut -d' ' -f1)" != "$2" ]; then
    curl -fsSL -o "$3" "$1"
    [ "$(sha256sum "$3" | cut -d' ' -f1)" = "$2" ] || { echo "$3 does not match its pinned SHA-256" >&2; exit 1; }
  fi
}

# 1. Windows CPython from the python.org installer, run silently: the NuGet build has no tkinter,
#    and the setup window needs Tcl/Tk (Include_tcltk=1). pip comes with it.
if [ ! -f "$PY" ]; then
  fetch "$PYTHON_URL" "$PYTHON_SHA256" "$WORK/dl/python-installer.exe"
  wine "$WORK/dl/python-installer.exe" /quiet InstallAllUsers=0 'TargetDir=C:\wozmod-python' Include_tcltk=1 \
    Include_pip=1 Include_doc=0 Include_dev=0 Include_test=0 Include_launcher=0 Include_tools=0 \
    Shortcuts=0 AssociateFiles=0 PrependPath=0 >/dev/null
fi
wine "$PY" -c "import tkinter" || { echo "the Windows Python has no tkinter" >&2; exit 1; }
wine "$PY" -m ensurepip >/dev/null

# 2. Python packages, exactly as locked (PyInstaller, Pillow, pytest), installed with their hashes
(cd "$ROOT" && uv export --frozen --no-emit-project --no-emit-package wozmod-native --group build --group dev \
  --format requirements-txt -o "$WORK/requirements.txt" >/dev/null)
wine "$PY" -m pip install -q --require-hashes --only-binary=:all: --no-warn-script-location -r "$(winpath "$WORK/requirements.txt")"

# 3. The Rust codecs, cross-built to an abi3 wheel with MinGW, and checked against the Python codecs under Wine
export PATH=$HOME/.cargo/bin:$PATH RUSTUP_TOOLCHAIN=stable
rustup target add x86_64-pc-windows-gnu >/dev/null
rm -f "$WORK"/wheels/*.whl
(cd "$ROOT/native" && CARGO_TARGET_DIR=$WORK/cargo-target \
  uvx --from "$MATURIN" maturin build --release --target x86_64-pc-windows-gnu -o "$WORK/wheels")
wine "$PY" -m pip install -q --force-reinstall --no-deps "$(winpath "$(ls "$WORK"/wheels/*.whl)")"
(cd "$ROOT" && PYTHONPATH=$(winpath "$ROOT/src") wine "$PY" -m pytest -q -p no:cacheprovider tests/test_native_codecs.py)

# 4. The pinned Windows ffmpeg, downloaded here because the Wine Python cannot rely on Windows certificates
read -r FFMPEG_URL FFMPEG_SHA256 < <(python3 -c \
  "import json; p = json.load(open('$ROOT/packaging/ffmpeg.json'))['windows']; print(p['url'], p['sha256'])")
fetch "$FFMPEG_URL" "$FFMPEG_SHA256" "$ROOT/build/packaging/cache/${FFMPEG_URL##*/}"

# 5. The zip: the same packaging/build.py as on Linux, now with Windows as sys.platform
cd "$ROOT"
wine "$PY" packaging/build.py --assets "$(winpath "$ASSETS")"

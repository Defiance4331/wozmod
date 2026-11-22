#!/bin/bash
# Build wozmod-<version>-windows.zip on Linux: the Windows program runs under Wine, with no GitHub Actions.
#
#   packaging/build_windows.sh --assets DIR --speech SPEECH --separator SEPARATOR
#
# The same three inputs as packaging/build.py, and all three folders must exist: the script checks them before it
# touches anything. DIR holds the release d3d9.dll and lettering.zip. SPEECH is the speech model folder
# (voices/phones.py MODEL), which the zip carries and the Windows phones are checked with. SEPARATOR is the Dream
# voice separator's folder (voices/separation.py MODEL), which the zip carries.
# The zip lands in build/packaging/. Needs: wine (64-bit), uv, rustup with a stable toolchain, curl,
# Xvfb, and MinGW (Fedora: sudo dnf install mingw64-gcc mingw64-binutils mingw64-winpthreads-static).
# Everything it sets up lives in WOZMOD_WIN_WORK (default work/windows-build) and is reused next time:
# a Wine prefix, Windows CPython (with Tcl/Tk), the locked Python packages and the cross-built Rust codecs.
# Downloads are pinned: Python below, packages in uv.lock (with hashes). ffmpeg is our own build (packaging/ffmpeg/), the
# proven program in the main checkout's work/release-assets/, checked against packaging/pins.json.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

usage="usage: packaging/build_windows.sh --assets DIR --speech SPEECH --separator SEPARATOR"
ASSETS= SPEECH= SEPARATOR=
while [ $# -gt 0 ]; do
  case $1 in
    --assets|--speech|--separator)
      [ $# -ge 2 ] || { echo "$1 needs a folder; $usage" >&2; exit 2; }
      [ -d "$2" ] || { echo "$1 $2: not a folder; $usage" >&2; exit 2; }
      folder=$(cd "$2" && pwd)
      case $1 in --assets) ASSETS=$folder;; --speech) SPEECH=$folder;; --separator) SEPARATOR=$folder;; esac
      shift 2;;
    *) echo "unknown argument $1; $usage" >&2; exit 2;;
  esac
done
[ -n "$ASSETS" ] || { echo "missing --assets; $usage" >&2; exit 2; }
[ -n "$SPEECH" ] || { echo "missing --speech; $usage" >&2; exit 2; }
[ -n "$SEPARATOR" ] || { echo "missing --separator; $usage" >&2; exit 2; }
WORK=${WOZMOD_WIN_WORK:-$ROOT/work/windows-build}
# The Windows CPython and maturin, pinned in packaging/windows-build.json (tools/windows_suite.py tests with the same)
read -r PYTHON_URL PYTHON_SHA256 MATURIN < <(python3 -c "import json; p = json.load(open('$ROOT/packaging/windows-build.json', \
  encoding='utf-8')); print(p['python']['url'], p['python']['sha256'], 'maturin==' + p['maturin'])")

for tool in wine uv curl x86_64-w64-mingw32-gcc x86_64-w64-mingw32-dlltool x86_64-w64-mingw32-windres Xvfb; do
  command -v "$tool" >/dev/null || { echo "missing $tool (see the header of this script)" >&2; exit 1; }
done
[ -x "$HOME/.cargo/bin/rustup" ] || { echo "missing rustup in ~/.cargo/bin" >&2; exit 1; }

export WINEPREFIX=$WORK/prefix WINEARCH=win64 WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
# Wine runs on a private virtual display, as in tools/windows_suite.py: the Python installer needs a display, and
# nothing appears on the desktop. -noreset: Xvfb can abort when it resets after its last client leaves.
DISPLAY_FILE=$(mktemp)
Xvfb -displayfd 3 -nolisten tcp -noreset -screen 0 1280x1024x24 3>"$DISPLAY_FILE" >/dev/null 2>&1 &
XVFB=$!
stop_display() { wineserver -k 2>/dev/null || true; wineserver -w 2>/dev/null || true; kill "$XVFB" 2>/dev/null || true; rm -f "$DISPLAY_FILE"; }
trap stop_display EXIT
for _ in $(seq 100); do [ -s "$DISPLAY_FILE" ] && break; sleep 0.1; done
[ -s "$DISPLAY_FILE" ] || { echo "Xvfb did not start" >&2; exit 1; }
export DISPLAY=:$(head -n1 "$DISPLAY_FILE")
unset WAYLAND_DISPLAY
winpath() { winepath -w "$1" 2>/dev/null; }
PY=$WORK/prefix/drive_c/wozmod-python/python.exe
mkdir -p "$WORK/dl"

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
(cd "$ROOT" && PYTHONPATH=$(winpath "$ROOT/src") wine "$PY" -m pytest -q -p no:cacheprovider tests/test_native_codecs.py \
  tests/test_voices_phones.py)
# The phones the mouths come from must be the same on Windows as on Linux, to the bit (packaging/speech_digest.py).
linux_digest=$(cd "$ROOT" && uv run --extra native python packaging/speech_digest.py "$SPEECH")
windows_digest=$(cd "$ROOT" && wine "$PY" "$(winpath "$ROOT/packaging/speech_digest.py")" "$(winpath "$SPEECH")" | tr -d '\r')
[ "$linux_digest" = "$windows_digest" ] || { echo "the Windows phones differ from Linux's: $windows_digest, $linux_digest" >&2; exit 1; }

# The licences of the Rust crates the codecs were just built from, and of what the toolchain linked in, for build.py
# to ship (packaging/notices.py): Wine cannot run cargo
NOTICES=$ROOT/build/packaging/notices/windows
rm -rf "$NOTICES" && mkdir -p "$NOTICES"
python3 -I "$ROOT/packaging/notices.py" crates windows "$NOTICES/RUST-CRATES-NOTICE.txt"

# 4. wozmod-setup.exe, the small launcher that opens the window (packaging/setup_launcher.c), with the icon and manifest
# the PyInstaller program before it had: PyInstaller's windowed icon (from the Windows PyInstaller just installed)
# and the manifest PyInstaller wrote
LAUNCHER=$ROOT/build/packaging/launcher
mkdir -p "$LAUNCHER"
cat > "$LAUNCHER/launcher.manifest" <<'MANIFEST'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{e2011457-1546-43c5-a5fe-008deee3d3f0}"/>
      <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"/>
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"/>
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
</assembly>
MANIFEST
printf 'LANGUAGE 0, 0\n1 ICON "%s"\n1 24 "%s"\n' "$WORK/prefix/drive_c/wozmod-python/Lib/site-packages/PyInstaller/bootloader/images/icon-windowed.ico" \
  "$LAUNCHER/launcher.manifest" > "$LAUNCHER/launcher.rc"
x86_64-w64-mingw32-windres "$LAUNCHER/launcher.rc" -O coff -o "$LAUNCHER/launcher.res"
x86_64-w64-mingw32-gcc -Os -s -municode -mwindows -Wall -Wextra -Werror -Wl,--no-insert-timestamp \
  -o "$LAUNCHER/wozmod-setup.exe" "$ROOT/packaging/setup_launcher.c" "$LAUNCHER/launcher.res"

# 5. Our Windows ffmpeg: the proven program in the main checkout's work/release-assets/, checked against its pin here
# and staged where build.py under Wine finds it (packaging/pins.py; built only when its recipe changes)
FFMPEG_DIR=$ROOT/build/packaging/ffmpeg/windows
FFMPEG=$(python3 -I "$ROOT/packaging/pins.py" windows)
rm -rf "$FFMPEG_DIR" && mkdir -p "$FFMPEG_DIR" && cp "$FFMPEG" "$FFMPEG_DIR/"

# 6. The zip: the same packaging/build.py as on Linux, now with Windows as sys.platform. Its step keys name the
# compiler that cross-built the codecs in step 3, which cannot run under Wine, so it is passed in.
cd "$ROOT"
WOZMOD_RUSTC_VERSION=$(cd "$ROOT/native" && rustc -V)
export WOZMOD_RUSTC_VERSION
wine "$PY" packaging/build.py --assets "$(winpath "$ASSETS")" --speech "$(winpath "$SPEECH")" \
  --separator "$(winpath "$SEPARATOR")"

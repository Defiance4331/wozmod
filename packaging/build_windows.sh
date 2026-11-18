#!/bin/bash
# Build wozmod-<version>-windows.zip on Linux: the Windows program runs under Wine, with no GitHub Actions.
#
#   packaging/build_windows.sh ASSETS_DIR
#
# ASSETS_DIR holds the release d3d9.dll and lettering.zip (as for packaging/build.py --assets).
# The zip lands in build/packaging/. Needs: wine (64-bit), uv, rustup with a stable toolchain, curl,
# and MinGW (Fedora: sudo dnf install mingw64-gcc mingw64-binutils mingw64-winpthreads-static), and WOZMOD_SPEECH
# naming the speech model folder (voices/phones.py MODEL), which the zip carries and the Windows phones are checked with.
# Everything it sets up lives in WOZMOD_WIN_WORK (default work/windows-build) and is reused next time:
# a Wine prefix, Windows CPython (with Tcl/Tk), the locked Python packages and the cross-built Rust codecs.
# Downloads are pinned: Python below, packages in uv.lock (with hashes). ffmpeg is our own build (packaging/ffmpeg/),
# made here on the Linux host and checked against packaging/ffmpeg.json; it also needs what packaging/ffmpeg/build.sh needs.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ASSETS=$(cd "${1:?usage: packaging/build_windows.sh ASSETS_DIR}" && pwd)
WORK=${WOZMOD_WIN_WORK:-$ROOT/work/windows-build}
# The Windows CPython and maturin, pinned in packaging/windows-build.json (tools/windows_suite.py tests with the same)
read -r PYTHON_URL PYTHON_SHA256 MATURIN < <(python3 -c "import json; p = json.load(open('$ROOT/packaging/windows-build.json', \
  encoding='utf-8')); print(p['python']['url'], p['python']['sha256'], 'maturin==' + p['maturin'])")

for tool in wine uv curl x86_64-w64-mingw32-gcc x86_64-w64-mingw32-dlltool x86_64-w64-mingw32-windres; do
  command -v "$tool" >/dev/null || { echo "missing $tool (see the header of this script)" >&2; exit 1; }
done
[ -x "$HOME/.cargo/bin/rustup" ] || { echo "missing rustup in ~/.cargo/bin" >&2; exit 1; }

export WINEPREFIX=$WORK/prefix WINEARCH=win64 WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
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
SPEECH=${WOZMOD_SPEECH:?set WOZMOD_SPEECH to the speech model folder (voices/phones.py MODEL)}
linux_digest=$(cd "$ROOT" && uv run --extra native python packaging/speech_digest.py "$SPEECH")
windows_digest=$(cd "$ROOT" && wine "$PY" "$(winpath "$ROOT/packaging/speech_digest.py")" "$(winpath "$SPEECH")" | tr -d '\r')
[ "$linux_digest" = "$windows_digest" ] || { echo "the Windows phones differ from Linux's: $windows_digest, $linux_digest" >&2; exit 1; }

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

# 5. Our Windows ffmpeg, built here because its build cannot run under Wine; build.py checks it against its pin
FFMPEG_DIR=$ROOT/build/packaging/ffmpeg/windows
read -r FFMPEG_FILE FFMPEG_SHA256 < <(python3 -c \
  "import json; p = json.load(open('$ROOT/packaging/ffmpeg.json'))['windows']; print(p['file'], p['sha256'])")
if [ "$(sha256sum "$FFMPEG_DIR/$FFMPEG_FILE" 2>/dev/null | cut -d' ' -f1)" != "$FFMPEG_SHA256" ]; then
  "$ROOT/packaging/ffmpeg/build.sh" windows "$FFMPEG_DIR"
fi

# 6. The zip: the same packaging/build.py as on Linux, now with Windows as sys.platform. Its step keys name the
# compiler that cross-built the codecs in step 3, which cannot run under Wine, so it is passed in.
cd "$ROOT"
WOZMOD_RUSTC_VERSION=$(cd "$ROOT/native" && rustc -V)
export WOZMOD_RUSTC_VERSION
wine "$PY" packaging/build.py --assets "$(winpath "$ASSETS")" --speech "$(winpath "$SPEECH")"

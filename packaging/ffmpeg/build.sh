#!/bin/bash
# Build wozmod's own ffmpeg: one static program with only the parts the installer uses (components.json), LGPL.
#
#   packaging/ffmpeg/build.sh linux|windows OUT_DIR
#
# OUT_DIR gets the program (ffmpeg-<VERSION>-linux or ffmpeg-<VERSION>-windows.exe), its SHA-256 and size for
# packaging/pins.json, and the four source archives it was built from (packaging/FFMPEG-NOTICE.txt). The work happens in
# work/ffmpeg-build/<target>/, rebuilt from scratch on every run. Needs, on Fedora: gcc, glibc-static, nasm, make,
# pkg-config, autoconf, automake, libtool, curl, and for Windows ucrt64-gcc, ucrt64-winpthreads-static and wine.
#
# The sources are the ones the BtbN autobuild wozmod shipped before (autobuild-2026-09-30-13-08) was built from, and
# the flags are its flags, so the voices come out byte for byte the same: FFmpeg's release/8.1 at n8.1.3-9, and the
# libogg, libvorbis and libsoxr commits its scripts pin. libvorbis's own configure adds -ffast-math on Linux, which makes the
# encoder's bytes depend on these flags; Windows builds against UCRT, as BtbN's does, for the same maths library.
set -euo pipefail

TARGET=${1:?usage: packaging/ffmpeg/build.sh linux|windows OUT_DIR}
OUT=$(mkdir -p "${2:?usage: packaging/ffmpeg/build.sh linux|windows OUT_DIR}" && cd "$2" && pwd)
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HERE=$ROOT/packaging/ffmpeg
VERSION=8.1.3-wozmod2  # FFmpeg's version, then our build of it: raise the number for any change to this script

FFMPEG_COMMIT=29e619e767cde9045a75c29bc9a8278ae7b3a98b  # n8.1.3-9-g29e619e767
FFMPEG_URL=https://github.com/FFmpeg/FFmpeg/archive/$FFMPEG_COMMIT.tar.gz
FFMPEG_SHA256=8e4699bcb6a311772e8f513cc72ce27328891b82a598b82e7cff3dfe580875a0
OGG_COMMIT=06a5e0262cdc28aa4ae6797627a783b5010440f0  # libogg 1.3.6 plus 7 commits
OGG_URL=https://github.com/xiph/ogg/archive/$OGG_COMMIT.tar.gz
OGG_SHA256=fd7ab06f9fe9ace6b75d4223cf9034c9648cbe9f16120a344ec82f27c605ca11
VORBIS_COMMIT=1b75110b5a2754ba1931d82dd83cb822b266a21d  # libvorbis 1.3.7 plus 37 commits
VORBIS_URL=https://github.com/xiph/vorbis/archive/$VORBIS_COMMIT.tar.gz
VORBIS_SHA256=bd0f08c8a63debdce27dd71de7d953f9c92e410d1cfe263220bcb49fff2ab749
SOXR_COMMIT=945b592b70470e29f917f4de89b4281fbbd540c0  # libsoxr 0.1.3 (the SourceForge repository's commit, mirrored)
SOXR_URL=https://github.com/chirlu/soxr/archive/$SOXR_COMMIT.tar.gz
SOXR_SHA256=ae970b44833c7dd2b088d5928b27c7f1796198ff5bc5386b90393aaf33e53d51

case $TARGET in
  linux)
    HOST=x86_64-pc-linux-gnu CROSS= EXE= PROGRAM=ffmpeg-$VERSION-linux
    CFLAGS="-static-libgcc -static-libstdc++ -O2 -pipe -fPIC -DPIC -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fstack-clash-protection -pthread"
    STAGE_CFLAGS="-fvisibility=hidden -fno-semantic-interposition"
    LDFLAGS="-static-libgcc -static-libstdc++ -O2 -pipe -fstack-protector-strong -fstack-clash-protection -Wl,-z,relro,-z,now -pthread -lm"
    TARGET_FLAGS=(--arch=x86_64 --target-os=linux --enable-pthreads)
    CMAKE_TARGET=()
    EXE_LDFLAGS="-static" ;;
  windows)
    HOST=x86_64-w64-mingw32ucrt CROSS=$HOST- EXE=.exe PROGRAM=ffmpeg-$VERSION-windows.exe
    CFLAGS="-static-libgcc -static-libstdc++ -O2 -pipe -D_FORTIFY_SOURCE=2 -fstack-protector-strong"
    STAGE_CFLAGS="-fno-semantic-interposition"
    LDFLAGS="-static-libgcc -static-libstdc++ -O2 -pipe -fstack-protector-strong"
    TARGET_FLAGS=(--arch=x86_64 --target-os=mingw32 --cross-prefix="$CROSS" --enable-w32threads)
    CMAKE_TARGET=(-DCMAKE_SYSTEM_NAME=Windows -DCMAKE_SYSTEM_PROCESSOR=x86_64 -DCMAKE_C_COMPILER="${CROSS}gcc"
                  -DCMAKE_RC_COMPILER="${CROSS}windres")
    EXE_LDFLAGS="-static -Wl,--no-insert-timestamp" ;;  # no link time in the PE header: the same bytes every build
  *) echo "usage: packaging/ffmpeg/build.sh linux|windows OUT_DIR" >&2; exit 2 ;;
esac
for tool in "${CROSS}gcc" nasm make cmake pkg-config autoreconf libtoolize curl sha256sum python3; do
  command -v "$tool" >/dev/null || { echo "missing $tool (see the header of this script)" >&2; exit 1; }
done

WORK=$ROOT/work/ffmpeg-build/$TARGET
PREFIX=$WORK/prefix
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1790713770  # FFmpeg's commit time: no build date leaks into the program
export PKG_CONFIG_LIBDIR=$PREFIX/lib/pkgconfig PKG_CONFIG_PATH=
rm -rf "$WORK" && mkdir -p "$WORK/src" "$ROOT/work/ffmpeg-build/dl"

fetch() {  # fetch URL SHA256: the archive, checked, under work/ffmpeg-build/dl/ (kept between runs)
  local file=$ROOT/work/ffmpeg-build/dl/${1##*/}
  if [ ! -f "$file" ] || [ "$(sha256sum "$file" | cut -d' ' -f1)" != "$2" ]; then
    curl -fsSL -o "$file.part" "$1" && mv "$file.part" "$file"
  fi
  [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$2" ] || { echo "$file does not match its pinned SHA-256" >&2; exit 1; }
  echo "$file"
}
unpack() {  # unpack ARCHIVE NAME: into $WORK/src/NAME
  mkdir -p "$WORK/src/$2" && tar -xzf "$1" -C "$WORK/src/$2" --strip-components=1
}
FFMPEG_ARCHIVE=$(fetch "$FFMPEG_URL" "$FFMPEG_SHA256")
OGG_ARCHIVE=$(fetch "$OGG_URL" "$OGG_SHA256")
VORBIS_ARCHIVE=$(fetch "$VORBIS_URL" "$VORBIS_SHA256")
SOXR_ARCHIVE=$(fetch "$SOXR_URL" "$SOXR_SHA256")
unpack "$FFMPEG_ARCHIVE" ffmpeg; unpack "$OGG_ARCHIVE" ogg; unpack "$VORBIS_ARCHIVE" vorbis; unpack "$SOXR_ARCHIVE" soxr

# libogg and libvorbis: static, with their own autotools build and BtbN's flags (its util/run_stage.sh adds the
# stage flags), in their source folders, so only relative paths reach the objects
for library in ogg vorbis; do
  (cd "$WORK/src/$library" && ./autogen.sh >/dev/null 2>&1 &&
   CFLAGS="$CFLAGS -I$PREFIX/include $STAGE_CFLAGS" LDFLAGS="$LDFLAGS -L$PREFIX/lib" \
     ./configure --prefix="$PREFIX" --host="$HOST" --disable-shared --enable-static --with-pic \
     $([ $library = vorbis ] && echo --disable-oggtest) >/dev/null &&
   make -j"$(nproc)" >/dev/null && make install >/dev/null)
done

# libsoxr (aresample's SoX resampler, which the Dream movies use): static, with its own CMake build, BtbN's flags and
# its two edits (a CMake range newer CMake accepts; the pkg-config file on Windows too). OpenMP is off: it only
# spreads channels over threads, and the replay shows the same bytes (packaging/ffmpeg/README.md).
(cd "$WORK/src/soxr" && sed -i 's/VERSION 3.1 /VERSION 3.1...3.10 /g' CMakeLists.txt && sed -i 's/NOT WIN32/1/g' src/CMakeLists.txt &&
 mkdir build && cd build &&
 CFLAGS="$CFLAGS -I$PREFIX/include $STAGE_CFLAGS" LDFLAGS="$LDFLAGS -L$PREFIX/lib" cmake .. "${CMAKE_TARGET[@]}" \
   -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" -DWITH_OPENMP=OFF -DBUILD_TESTS=OFF \
   -DBUILD_EXAMPLES=OFF -DBUILD_SHARED_LIBS=OFF >/dev/null &&
 make -j"$(nproc)" >/dev/null && make install >/dev/null)

# FFmpeg: nothing but the components the install uses, and the ffmpeg program alone
cd "$WORK/src/ffmpeg"
mapfile -t ENABLE < <(python3 -I "$HERE/components.py" configure "$HERE/components.json" .)
echo "$VERSION" > VERSION  # FFmpeg reads its version from here when the source has no .git
# Relative paths, and a prefix that is never installed to: the configure line is kept in the program (-buildconf).
./configure --prefix=/ffmpeg --pkg-config=pkg-config --pkg-config-flags=--static "${TARGET_FLAGS[@]}" \
  --disable-everything --disable-autodetect --disable-programs --enable-ffmpeg --disable-doc --disable-debug \
  --disable-network --disable-avdevice --disable-swscale --enable-static --disable-shared \
  "${ENABLE[@]}" --extra-cflags=-I../../prefix/include --extra-ldflags=-L../../prefix/lib --extra-ldexeflags="$EXE_LDFLAGS" \
  --extra-libs=-lm \
  > "$WORK/configure.log"
make -j"$(nproc)" "ffmpeg$EXE" >/dev/null
"${CROSS}strip" -o "$OUT/$PROGRAM" "ffmpeg$EXE"
# No path of this machine in the program: the same build on another machine gives the same bytes
if grep -q -a -F "$ROOT" "$OUT/$PROGRAM"; then echo "$OUT/$PROGRAM holds the path $ROOT" >&2; exit 1; fi
# Every component the install uses answers by its name (configure skips a wrong name without a word)
if [ "$TARGET" = windows ]; then
  WINEPREFIX=$ROOT/work/ffmpeg-build/wine WINEDEBUG=-all python3 -I "$HERE/components.py" check "$HERE/components.json" \
    "$OUT/$PROGRAM" --runner wine
else
  python3 -I "$HERE/components.py" check "$HERE/components.json" "$OUT/$PROGRAM"
fi

cp "$FFMPEG_ARCHIVE" "$OUT/ffmpeg-$FFMPEG_COMMIT.tar.gz"
cp "$OGG_ARCHIVE" "$OUT/libogg-$OGG_COMMIT.tar.gz"
cp "$VORBIS_ARCHIVE" "$OUT/libvorbis-$VORBIS_COMMIT.tar.gz"
cp "$SOXR_ARCHIVE" "$OUT/libsoxr-$SOXR_COMMIT.tar.gz"
(cd "$OUT" && sha256sum "$PROGRAM" > "$PROGRAM.sha256" && stat -c '%s %n' "$PROGRAM" && cat "$PROGRAM.sha256")

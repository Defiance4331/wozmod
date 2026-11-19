# wozmod's ffmpeg

Each release zip carries our own static build of FFmpeg at `_internal/ffmpeg/bin/`, with only the parts the installer
uses. `packaging/pins.json` pins it by name, SHA-256 and size per system, beside the `d3d9.dll` and `lettering.zip` pins. The proven programs stay in the main
checkout's `work/release-assets/` (untracked, outside `tools/cleanup.py`): `packaging/build.py` and
`packaging/build_windows.sh` take them from there, checked against the pin, and so does the timed install test
(`tests/test_install_e2e.py`), so its time is a player's. Nothing builds ffmpeg but the procedure below, which runs only
when the recipe changes (installer decision 56). A checkout's own installs run the `ffmpeg` on `PATH`.

A lost program comes back from the latest published release zip that carries it (`_internal/ffmpeg/bin/ffmpeg`, or
`ffmpeg.exe` for Windows, checked against the pin), else from the procedure below. A build tool downloads nothing.

| File | Holds |
|---|---|
| `components.json` | the demuxers, muxers, decoders, encoders, filters, protocols and outside libraries a full install runs, by ffmpeg's names |
| `components.py` | records an install's ffmpeg runs, derives `components.json`, turns it into configure flags, checks a build, and replays every recorded run with another build |
| `build.sh` | builds the program for Linux or Windows from pinned sources, reproducibly |
| `licenses/` | FFmpeg's, libogg's, libvorbis's and libsoxr's licence texts, which the zip ships beside `packaging/FFMPEG-NOTICE.txt` |

## When the installer's ffmpeg use changes

A new ffmpeg option, input format or codec in `formats/audio.py`, `voices/` (with `voices/dream_movies.py`, which runs
ffmpeg itself) or `text/contexts.py` needs a component the build lacks, and the install then stops with ffmpeg's error. Record a full dev install with the ffmpeg you trust, and
derive the list again:

```
python3 packaging/ffmpeg/components.py shim work/ffmpeg-record --real "$(command -v ffmpeg)" \
  --log work/ffmpeg-record.jsonl --keep work/ffmpeg-corpus
PATH=$PWD/work/ffmpeg-record:$PATH uv run wozmod install ...   # every input on the command line
python3 packaging/ffmpeg/components.py summarize work/ffmpeg-record.jsonl --write packaging/ffmpeg/components.json
```

The summary also lists the codecs ffmpeg found no parser for. A build that lacks a parser the trusted ffmpeg used
shows up there as a codec the trusted run did not list.

## A new build

Only for a change to the recipe: the FFmpeg version, `components.json`, or the source of a library it bundles
(libvorbis, libogg, libsoxr), with `build.sh` that names them. A compiler update is no reason: the 2026-11-19 rebuild
with Fedora 45's compilers gave other bytes but the same outputs on every one of an install's 2,775 ffmpeg runs, on
both systems.

1. Raise the build number in `VERSION` in `build.sh` (`8.1.3-wozmod1` → `8.1.3-wozmod2`) for any change to it or to
   `components.json`.
2. Build each program, under the machine lock: `python3 tools/machine_lock.py packaging/ffmpeg/build.sh linux OUT`,
   and the same with `windows`. Building twice gives the same bytes.
3. Check that it converts the same: `components.py replay LOG CORPUS PROGRAM --write HASHES` with the corpus kept
   above, for the old and the new program of each system (`--runner wine` for Windows), and compare the two hash
   files. Every run must match, or each difference explained. Then install the Windows zip on Windows (a real
   install, not Wine).
4. Put each program's name, SHA-256 and size in `packaging/pins.json`, and the programs in `work/release-assets/`.
   The step keys cover the pin, so the next update rebuilds the voices, text and art with the new program.

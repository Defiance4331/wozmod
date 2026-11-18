"""packaging/ffmpeg/components.py: the ffmpeg components a recorded install used, which build.sh enables."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging/ffmpeg/components.py"
spec = importlib.util.spec_from_file_location("components", SCRIPT)
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)

# What ffmpeg -loglevel verbose prints for one run with two inputs and two outputs (the lines the record reads).
LOG = """\
Stream mapping:
  Stream #0:0 -> #0:0 (xma2 (native) -> vorbis (libvorbis))
  Stream #1:0 -> #1:0 (adpcm_psx (native) -> pcm_s16le (native))
[format_out_#0:0 @ 0x1] auto-inserting filter 'auto_aresample_0' between the filter 'Parsed_pan_1' and the filter 'format_out_#0:0'
[Parsed_pan_1 @ 0x3] o0 = 0.5 i0 + 0.5 i1
[auto_aresample_0 @ 0x4] ch:1 chl:mono fmt:s16 r:44100Hz -> ch:1 chl:mono fmt:fltp r:48000Hz
"""
ARGS = ["-hide_banner", "-loglevel", "error", "-filter_threads", "1",
        "-threads", "1", "-f", "wav", "-i", "/t/0.in", "-f", "vag", "-i", "/t/1.in",
        "-map", "0:a", "-af", "asetpts=round(N/SR/TB),pan=mono|c0=0.5*c0+0.5*c1", "-ar", "48000", "-c:a", "libvorbis",
        "-q:a", "7", "-f", "ogg", "/t/0.out", "-map", "1:a", "-ac", "1", "-ar", "16000", "-f", "s16le", "/t/1.out"]


# A Dream movie's audio read as ffmpeg finds it (no -f before -i), resampled with the SoX resampler, to a pipe.
MOVIE_ARGS = ["-hide_banner", "-loglevel", "error", "-nostdin", "-i", "/t/movie200.wmv", "-map", "0:a:0", "-af",
              "asetpts=round(N/SR/TB),aresample=48000:resampler=soxr", "-f", "f32le", "-"]
MOVIE_LOG = """\
Input #0, asf, from '/t/movie200.wmv':
Stream mapping:
  Stream #0:1 -> #0:0 (wmav2 (native) -> pcm_f32le (native))
Output #0, f32le, to 'pipe:':
"""


def test_the_record_lists_every_component_a_run_used():
    # Bugs: a filter that logs nothing at verbose (asetpts) is left out, so the built ffmpeg refuses the voice
    # filter chain; ffmpeg's own encoder of a codec ("pcm_s16le (native)") is recorded as "native"; a format ffmpeg
    # found by itself (the movies' ASF), the pipe a run writes to, or the library its filter options name (libsoxr)
    # is missed, so the built ffmpeg cannot convert the Dream movies.
    assert components.used([{"args": ARGS, "code": 0, "log": LOG}, {"args": MOVIE_ARGS, "code": 0, "log": MOVIE_LOG}]) == {
        "demuxer": ["asf", "vag", "wav"],
        "muxer": ["f32le", "ogg", "s16le"],
        "decoder": ["adpcm_psx", "wmav2", "xma2"],
        "encoder": ["libvorbis", "pcm_f32le", "pcm_s16le"],
        "filter": ["aformat", "anull", "aresample", "asetpts", "pan"],
        "protocol": ["file", "pipe"],
        "library": ["libsoxr", "libvorbis"],
    }


def test_a_run_whose_format_neither_its_log_nor_its_arguments_name_is_refused():
    # Bug: an output without its own -f takes the input's format from before -i, so the record names a muxer the
    # run never used and misses the one ffmpeg chose, which the built ffmpeg then lacks.
    args = ["-f", "wav", "-i", "/t/0.in", "-map", "0:a", "/t/0.ogg"]
    with pytest.raises(SystemExit, match="neither in its log nor given with -f"):
        components.used([{"args": args, "code": 0, "log": ""}])


@pytest.mark.linux_only("ffmpeg is configured and built for both systems on Linux (packaging/ffmpeg/build.sh)")
def test_each_name_becomes_the_component_configure_enables(tmp_path):
    # Bugs: the decoder ffmpeg calls atrac3plus is enabled as atrac3plus, or the format s16le as muxer s16le; configure
    # skips an unknown name without a word, and the built ffmpeg cannot read the PSP clips or write raw PCM.
    source = tmp_path / "ffmpeg"
    for folder in ("libavcodec", "libavformat", "libavfilter"):
        (source / folder).mkdir(parents=True)
    (source / "libavcodec/atrac3plusdec.c").write_text(
        'const FFCodec ff_atrac3p_decoder = {\n    .p.name         = "atrac3plus",\n    .p.type = 1,\n};\n'
        'const FFCodec ff_atrac3pal_decoder = {\n    .p.name         = "atrac3plusal",\n};\n', encoding="utf-8")
    lists = {"decoders": "atrac3p atrac3pal xma2", "muxers": "ogg pcm_s16le pcm_s16be", "demuxers": "wav",
             "encoders": "", "filters": "pan", "protocols": "file"}
    (source / "configure").write_text("#!/bin/sh\ncase $1 in\n" + "".join(
        f"  --list-{kind}) echo '{names}' ;;\n" for kind, names in lists.items()) + "esac\n", encoding="utf-8")
    (source / "configure").chmod(0o755)
    names = {"decoder": ["atrac3plus", "xma2"], "muxer": ["ogg", "s16le"], "demuxer": ["wav"], "filter": ["pan"],
             "protocol": ["file"]}
    assert components.configure_flags(names, source) == [
        "--enable-demuxer=wav", "--enable-muxer=ogg", "--enable-muxer=pcm_s16le", "--enable-decoder=atrac3p",
        "--enable-decoder=xma2", "--enable-filter=pan", "--enable-protocol=file"]

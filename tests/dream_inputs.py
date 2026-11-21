"""The real inputs of the Z Dream scenes, for the slow tests: the stock game's clips and event files (WOZMOD_GAME) and
the WO2 PSP movies (WOZMOD_DONORS)."""

from __future__ import annotations

import os
from array import array
from pathlib import Path

from wozmod.voices.dream_events import RATE, scene_lines


def stock_clips(game: Path, voices: list[int], rate: int = RATE) -> dict[int, array]:
    """The stock clips of voices as mono 32-bit float samples at rate."""
    from wozmod import game_files
    from wozmod.formats.audio import _ffmpeg_each, kovs_ogg

    takes = game_files.voices(game).clips(voices)
    oggs = [kovs_ogg(takes[v]) for v in voices]
    pcms = _ffmpeg_each([(["-f", "ogg"], ogg, ["-ac", "1", "-ar", str(rate), "-f", "f32le"]) for ogg in oggs], len(oggs))
    return {v: array("f", pcm) for v, pcm in zip(voices, pcms, strict=True)}


def scene_tracks(o: int):
    """(the lines, the stock clips, the PSP's English track, its Japanese, its English as the separator takes it) of
    Z Dream scene o."""
    from wozmod.formats.linkdata import archive
    from wozmod.formats.pmf import audio_tracks
    from wozmod.voices import separation
    from wozmod.voices.dream_events import DREAM_EVENTS, ENGLISH, JAPANESE, _decoded_track, _track

    game, psp = Path(os.environ["WOZMOD_GAME"]), Path(os.environ["WOZMOD_DONORS"]) / "wo2-psp"
    lines = scene_lines(o, archive(game / "LINKDATA2").read([o])[o])
    tracks = audio_tracks((psp / DREAM_EVENTS[o]).read_bytes())
    return (lines, stock_clips(game, [line.voice for line in lines]), _track(tracks[ENGLISH]), _track(tracks[JAPANESE]),
            _decoded_track(tracks[ENGLISH], 2, separation.RATE).tobytes())

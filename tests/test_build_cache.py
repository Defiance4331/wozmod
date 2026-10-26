"""The developer build cache (tools/build_cache.py): a step is reused only when its key (wozmod.install.step_keys,
whose derivation tests/test_step_keys.py covers) is the one it was kept under, and its kept files are untouched."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import build_cache
from wozmod import install as installer
from wozmod import movies, remembered
from wozmod.donors import Donor, DonorFile, DonorsFolder
from wozmod.progress import Progress

# Through the installer's real _build: fake disc reads and steps that record each run in the game folder.

MOVIE = b"movie bytes"


def _fake_text(game, donors, out):
    _ran(game, "text")
    (out / "wozmod").mkdir(parents=True, exist_ok=True)
    (out / "wozmod/table.bin").write_bytes(b"table of " + DonorsFolder(donors).folder("wo2-x360").joinpath("link_vodat.bdx").read_bytes())


def _fake_voices(game, donors, out, jobs):
    _ran(game, "voices")
    (out / "wozmod/voice").mkdir(parents=True, exist_ok=True)
    (out / "wozmod/voice/Voice.bin").write_bytes(b"voices of " + DonorsFolder(donors).folder("wo2-x360").joinpath("link_vodat.bdx").read_bytes())


def _fake_art(game, donors, lettering, staging, out, jobs):
    _ran(game, "art")
    (out / "wozmod/hires").mkdir(parents=True, exist_ok=True)
    (out / "wozmod/hires/hires.bin").write_bytes(b"art of " + Path(lettering).read_bytes())


def _ran(game, step):
    with open(game / "runs.txt", "a") as runs:
        runs.write(step + "\n")


def test_a_reused_step_puts_the_bytes_its_build_wrote_and_runs_nothing(tmp_path, monkeypatch):
    """Bug: a reused step lands in the wrong folder or drops a file, so the install differs from a build; a hit
    still runs the step or reads the movies from the disc; a kept file written to after the build is reused; or a
    changed donor still gets the old output."""
    game, donor = tmp_path / "game", {"bytes": b"donor one"}
    game.mkdir()
    (tmp_path / "lettering.zip").write_bytes(b"bundle")
    (tmp_path / "d3d9.dll").write_bytes(b"dll")
    inputs = {**{flag: tmp_path / flag for flag in ("wo1-x360", "wo2-x360", "wo2-psp")},
              "lettering": tmp_path / "lettering.zip", "dll": tmp_path / "d3d9.dll"}
    reads = []

    def extract(flag, image, donor_disc, destination, files, *rest, **options):
        movie = destination.name == "movie"
        reads.append("movies" if movie else flag)
        for name in files:
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            (destination / name).write_bytes(MOVIE if movie else donor["bytes"])

    movie = DonorFile("voice/movie001.wmv", "movie001.wmv", len(MOVIE), hashlib.sha256(MOVIE).hexdigest())
    monkeypatch.setattr(movies, "movie_donors", lambda: {"wo2-x360": Donor("wo2-x360", "xdvdfs", (movie,))})
    monkeypatch.setattr(installer, "_extract", extract)
    monkeypatch.setattr(installer, "_place_programs", lambda inputs, donors: None)
    monkeypatch.setattr(remembered, "settings_path", lambda: tmp_path / "config/settings.json")
    for name, fake in (("_text_table", _fake_text), ("_voice_banks", _fake_voices), ("_menu_art", _fake_art)):
        monkeypatch.setattr(installer, name, fake)
    cache = build_cache.BuildCache(tmp_path / "cache", here={"machine": "this one"})

    def build(run, cache=None):
        (game / "runs.txt").unlink(missing_ok=True)
        reads.clear()
        out, _ = installer._build(game, inputs, tmp_path / run, Progress(), 1, lambda wait=True: None, cache=cache)
        runs = sorted((game / "runs.txt").read_text().split()) if (game / "runs.txt").exists() else []
        return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}, runs, list(reads)

    plain, runs, _ = build("plain")
    assert runs == ["art", "text", "voices"]
    assert build("cold", cache) == (plain, runs, ["wo1-x360", "wo2-x360", "wo2-psp", "movies"])
    assert build("warm", cache) == (plain, [], ["wo1-x360", "wo2-x360", "wo2-psp"])

    with open(tmp_path / "cold/out/wozmod/table.bin", "r+b") as built:  # the cache keeps this very file
        built.write(b"TABLE")
    assert build("edited", cache) == (plain, ["text"], ["wo1-x360", "wo2-x360", "wo2-psp"])
    assert build("again", cache) == (plain, [], ["wo1-x360", "wo2-x360", "wo2-psp"])

    donor["bytes"] = b"donor two"  # a disc member with another pin: only the voices read it
    from wozmod.donors.inventory import DONORS
    files = tuple(dataclasses.replace(f, sha256="0" * 64) if f.path == "link_vodat.bdx" else f for f in DONORS["wo2-x360"].files)
    monkeypatch.setitem(DONORS, "wo2-x360", dataclasses.replace(DONORS["wo2-x360"], files=files))
    changed, runs, _ = build("changed", cache)
    assert runs == ["voices"]
    assert changed == {**plain, "wozmod/voice/Voice.bin": b"voices of donor two"}

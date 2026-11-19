"""packaging/pins.py: the release pins the release build writes, and the proven ffmpeg packaging takes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / f"packaging/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pins = _load("pins")


def test_the_release_build_pins_the_files_it_ships_and_the_pins_read_back(tmp_path, monkeypatch):
    """Bug: the release build ships a d3d9.dll or lettering.zip whose pin it did not write (one pin is left as it
    was), or writing them drops the ffmpeg pins beside them."""
    monkeypatch.setattr(pins, "ROOT", tmp_path)
    (tmp_path / "packaging").mkdir()
    ffmpeg = {"linux": {"file": "f", "sha256": "ff", "size": 1}}
    (tmp_path / pins.PINS).write_text(json.dumps({"d3d9.dll": {"size": 1, "sha256": "old"},
                                                  "lettering.zip": {"size": 1, "sha256": "old"}, "ffmpeg": ffmpeg}),
                                      encoding="utf-8")
    (tmp_path / "d3d9.dll").write_bytes(b"dll bytes")
    (tmp_path / "lettering.zip").write_bytes(b"lettering bytes")
    pins.write_assets(tmp_path / "d3d9.dll", tmp_path / "lettering.zip")
    assert pins.asset_pin("d3d9.dll") == (9, hashlib.sha256(b"dll bytes").hexdigest())
    assert pins.asset_pin("lettering.zip") == (15, hashlib.sha256(b"lettering bytes").hexdigest())
    assert pins.pins()["ffmpeg"] == ffmpeg


def _proven(tmp_path: Path, data: bytes = b"proven ffmpeg") -> Path:
    """A tree whose pins.json pins `data` as the Linux ffmpeg, and a release-assets folder holding it: (the folder)."""
    (tmp_path / "packaging").mkdir()
    pin = {"file": "ffmpeg-x-linux", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    (tmp_path / "packaging/pins.json").write_text(json.dumps({"ffmpeg": {"linux": pin}}), encoding="utf-8")
    assets = tmp_path / "release-assets"
    assets.mkdir()
    (assets / "ffmpeg-x-linux").write_bytes(data)
    return assets


def test_a_binary_that_differs_from_the_pin_is_not_used(tmp_path, monkeypatch):
    """Bug: a program of the same name and size but other bytes (another build, a damaged copy) is shipped or timed
    as the proven one; or a missing one is not met with the way to get it back."""
    monkeypatch.setattr(pins, "ROOT", tmp_path)
    assets = _proven(tmp_path)
    assert pins.ffmpeg_problem("linux", assets / "ffmpeg-x-linux") is None
    (assets / "ffmpeg-x-linux").write_bytes(b"proven ffmpex")  # same size
    assert "is not the program packaging/pins.json pins" in pins.ffmpeg_problem("linux", assets / "ffmpeg-x-linux")
    missing = pins.ffmpeg_problem("linux", assets / "none")
    assert "missing" in missing and "latest published wozmod-*-linux.zip" in missing and "README" in missing


def test_packaging_takes_the_proven_ffmpeg_and_never_builds_one(tmp_path, monkeypatch):
    """Bug: packaging rebuilds ffmpeg although its recipe did not change, so a compiler update ships unproven bytes
    (decision 56: only a recipe change is rebuilt, by the README procedure)."""
    build = _load("build")
    monkeypatch.setattr(build, "WINDOWS", False)
    monkeypatch.setattr(build, "PLATFORM", "linux")
    monkeypatch.setattr(build.pins, "ROOT", tmp_path)
    assets = _proven(tmp_path)
    monkeypatch.setattr(build.pins, "release_assets", lambda: assets)

    def no_build(*args, **kwargs):
        raise AssertionError(f"ran {args[0]}")

    monkeypatch.setattr(subprocess, "run", no_build)
    assert build.prepare_ffmpeg() == assets / "ffmpeg-x-linux"

"""Where wozmod finds data/, runtime/ and ffmpeg, in the source tree and in a PyInstaller bundle."""

import os
import stat
import sys

import pytest

from wozmod import resources
from wozmod.formats.audio import _ffmpeg_each

POSIX = pytest.mark.skipif(os.name == "nt", reason="the fake ffmpeg is a shell script")


def fake_ffmpeg(path, text):
    """A program that writes `text` to the last file named on its command line, as ffmpeg writes its output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\nfor last; do :; done\necho {text} > "$last"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_source_tree_root_holds_the_files_the_install_reads():
    # Bug: the root points one folder too high or too low, so every data file is "missing" when run from the source.
    for name in ("data/text/table.json", "data/art/lettering.json", "data/art/faces/mainvec.py",
                 "runtime/english_runtime.c"):
        assert (resources.root() / name).is_file(), name


def test_bundle_root_is_the_folder_pyinstaller_unpacks(tmp_path, monkeypatch):
    # Bug: a packaged program still looks beside the source file and finds no data/.
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.root() == tmp_path


@POSIX
def test_bundled_ffmpeg_wins_over_the_one_on_path(tmp_path, monkeypatch):
    # Bug: shutil.which("ffmpeg") or a bare "ffmpeg" in the command finds the player's own ffmpeg first.
    fake_ffmpeg(tmp_path / "bundle/ffmpeg/bin/ffmpeg", "bundled")
    fake_ffmpeg(tmp_path / "path/ffmpeg", "path")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    assert resources.ffmpeg() == str(tmp_path / "bundle/ffmpeg/bin/ffmpeg")
    assert _ffmpeg_each([([], b"clip", [])]) == [b"bundled\n"]


@POSIX
def test_ffmpeg_on_path_is_used_when_nothing_is_bundled(tmp_path, monkeypatch):
    # Bug: developers (no bundle) lose their PATH ffmpeg.
    fake_ffmpeg(tmp_path / "path/ffmpeg", "path")
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    assert resources.ffmpeg() == str(tmp_path / "path/ffmpeg")
    assert _ffmpeg_each([([], b"clip", [])]) == [b"path\n"]


def test_no_ffmpeg_anywhere_is_reported_as_none(tmp_path, monkeypatch):
    # Bug: a fallback name instead of None, so check_inputs never says that ffmpeg is missing.
    monkeypatch.setenv("PATH", str(tmp_path))
    assert resources.ffmpeg() is None


def test_lzp2_cache_key_needs_no_source_file(monkeypatch):
    # Bug: the key read the codec's .py file, which a packaged program does not have (FileNotFoundError mid-install).
    from wozmod.art import build

    namespace = {}
    exec(compile("def encode(data):\n    return data\n", "wozmod/not/on/disk.py", "exec"), namespace)
    monkeypatch.setattr(build, "encode_lzp2", namespace["encode"])
    build._lzp2_codec.cache_clear()
    try:
        assert len(build._lzp2_codec()) == 32
    finally:
        build._lzp2_codec.cache_clear()


def test_packaged_install_takes_the_dll_and_lettering_from_inside_the_bundle(tmp_path, monkeypatch, capsys):
    # Bug: the defaults still point beside the program (where players no longer see the files), resolve against
    # the working folder, or leak into source runs; or --dll and --lettering show up in --help.
    from wozmod import __main__ as cli
    seen = {}
    monkeypatch.setattr(cli, "install", lambda game, inputs, **_: seen.update(inputs))
    discs = [arg for name in ("wo1", "wo1-x360", "x360", "psp", "x360-tool", "psp-tool") for arg in (f"--{name}", name)]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "release" / "wozmod"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "release/_internal"), raising=False)
    assert cli.main(["install", "--game", "g", *discs]) == 0
    assert (seen["dll"], seen["lettering"]) == (tmp_path / "release/_internal/d3d9.dll",
                                                tmp_path / "release/_internal/lettering.zip")
    monkeypatch.setattr(sys, "frozen", False)
    assert cli.main(["install", "--game", "g", *discs]) == 2
    with pytest.raises(SystemExit):
        cli.main(["install", "--help"])
    assert "--dll" not in capsys.readouterr().out

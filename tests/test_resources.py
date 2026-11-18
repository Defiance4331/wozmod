"""Where wozmod finds data/, runtime/ and ffmpeg, in the source tree and in a PyInstaller bundle."""

import sys

import pytest

import fake_program
from wozmod import resources
from wozmod.formats.audio import _ffmpeg_each


def fake_ffmpeg(path, text):
    """A program that writes `text` to the last file named on its command line, as ffmpeg writes its output."""
    return fake_program.program(path, f"import sys\nfrom pathlib import Path\nPath(sys.argv[-1]).write_bytes({text!r}.encode() + b'\\n')\n")


def test_source_tree_root_holds_the_files_the_install_reads():
    # Bug: the root points one folder too high or too low, so every data file is "missing" when run from the source.
    for name in ("data/text/table.json", "data/art/lettering.json", "data/art/faces/mainvec.py",
                 "runtime/english_runtime.c"):
        assert (resources.root() / name).is_file(), name


@pytest.mark.parametrize("bundled, on_path, found", [
    pytest.param(True, True, "bundled", id="bundled-over-path"),
    pytest.param(False, True, "path", id="path-without-bundle"),
    pytest.param(False, False, None, id="neither"),
])
def test_ffmpeg_is_the_bundled_one_else_the_one_on_path_else_none(tmp_path, monkeypatch, bundled, on_path, found):
    # Bugs: shutil.which("ffmpeg") or a bare "ffmpeg" in the command finds the player's own ffmpeg before the bundled
    # one; developers (no bundle) lose their PATH ffmpeg; or a fallback name instead of None, so check_inputs never
    # says that ffmpeg is missing. The fakes write their own name, so the output says which one ran.
    fakes = {}
    if bundled:
        fakes["bundled"] = fake_ffmpeg(tmp_path / "bundle/ffmpeg/bin/ffmpeg", "bundled")
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    if on_path:
        fakes["path"] = fake_ffmpeg(tmp_path / "path/ffmpeg", "path")
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    assert resources.ffmpeg() == (found and str(fakes[found]))
    if found:
        assert _ffmpeg_each([([], b"clip", [])]) == [f"{found}\n".encode()]


def test_lzp2_cache_key_needs_no_source_file(monkeypatch):
    # Bug: the key read the codec's .py file, which a packaged program does not have (FileNotFoundError mid-install).
    from wozmod.art import packing

    namespace = {}
    exec(compile("def encode(data):\n    return data\n", "wozmod/not/on/disk.py", "exec"), namespace)
    monkeypatch.setattr(packing, "encode_lzp2", namespace["encode"])
    packing._lzp2_codec.cache_clear()
    try:
        assert len(packing._lzp2_codec()) == 32
    finally:
        packing._lzp2_codec.cache_clear()


def test_packaged_install_takes_the_dll_and_lettering_from_inside_the_bundle(tmp_path, monkeypatch, capsys):
    # Bug: the defaults still point beside the program (where players no longer see the files), resolve against
    # the working folder, or leak into source runs; or --dll and --lettering show up in --help.
    from wozmod import __main__ as cli
    seen = {}
    monkeypatch.setattr(cli, "install", lambda game, inputs, **_: seen.update(inputs))
    discs = [arg for name in ("wo1-x360", "wo2-x360", "wo2-psp", "x360-tool", "psp-tool") for arg in (f"--{name}", name)]
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

"""The terminal front end: it asks for what is missing, checks each answer, and does not prompt without a terminal."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

from wozmod import __main__ as cli
from wozmod import remembered


class Terminal(io.StringIO):
    def isatty(self):
        return True


class Quiet:
    def __call__(self, progress):
        pass

    def finish(self, progress, message):
        pass


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)


def answer(monkeypatch, *lines):
    monkeypatch.setattr(sys, "stdin", Terminal("".join(line + "\n" for line in lines)))


def test_without_a_terminal_it_lists_the_missing_options_and_exits_2(monkeypatch, capsys):
    # Bug: the command waits for input that never comes (a script hangs), or exits 0 after saying nothing.
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli.main(["install", "--wo1", "a.iso"]) == 2
    error = capsys.readouterr().err
    options = set(re.findall(r"--[\w-]+", error))
    assert {"--game", "--wo1-x360", "--x360", "--psp"} <= options and "--wo1" not in options
    assert cli.main([]) == 2  # no command and no terminal: the help, not a menu


def test_it_asks_for_each_missing_path_checks_the_answer_and_strips_quotes(monkeypatch, tmp_path, capsys):
    # Bug: a dragged path keeps its quotes, a wrong answer is accepted, or the remembered path is not offered.
    remembered.save_settings({"x360": "/remembered/x360.iso"})
    checks, seen = [], {}
    monkeypatch.setattr(cli, "check_input", lambda name, path: checks.append((name, path)) or (
        "Not a folder." if path == Path("wrong") else None))
    ran = []
    monkeypatch.setattr(cli, "decrypt_with_tool", lambda name, tool, image: ran.append((name, tool, image)) or b"program")
    monkeypatch.setattr(cli, "install", lambda game, inputs, **kw: seen.update(game=game, **inputs))
    monkeypatch.setattr(cli, "TerminalView", Quiet)
    answer(monkeypatch, "wrong", f"'{tmp_path}'", '"/discs/wo1.iso"', "/discs/wo1-x360.iso", "", "/discs/psp.iso",
           "/tools/x", '"/my tools/p"')

    assert cli.main(["install"]) == 0

    assert (seen["game"], seen["wo1"], seen["x360"], seen["psp"]) == (
        tmp_path, Path("/discs/wo1.iso"), Path("/remembered/x360.iso"), Path("/discs/psp.iso"))
    assert (seen["x360-tool"], seen["psp-tool"]) == (Path("/tools/x"), Path("/my tools/p"))
    assert ran == [("wo1-x360", Path("/tools/x"), Path("/discs/wo1-x360.iso")),
                   ("x360", Path("/tools/x"), Path("/remembered/x360.iso")),
                   ("psp", Path("/my tools/p"), Path("/discs/psp.iso"))]  # each is run on each of its discs' files
    assert "Not a folder." in capsys.readouterr().out
    assert remembered.load_settings()["wo1"] == "/discs/wo1.iso"  # the window offers it next time


def test_no_arguments_in_a_terminal_shows_the_menu(monkeypatch, tmp_path):
    # Bug: the menu choice is ignored, so "Uninstall" runs the install (or nothing).
    seen = {}
    monkeypatch.setattr(cli, "uninstall", lambda game, log, keep_settings: seen.update(game=game, keep=keep_settings) or {})
    monkeypatch.setattr(cli, "check_input", lambda name, path: None)
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    answer(monkeypatch, "2", str(tmp_path), "n")
    assert cli.main([]) == 0
    assert seen == {"game": tmp_path, "keep": False}


@pytest.mark.parametrize("flags, typed, keep", [
    (["--remove-settings"], None, False), (["--keep-settings"], None, True),
    ([], "n", False), ([], "", True),
])
def test_uninstall_asks_about_the_settings_only_when_no_flag_says(monkeypatch, tmp_path, flags, typed, keep):
    # Bug: a flag is ignored (settings deleted against --keep-settings), or an unflagged run does not ask.
    seen = {}
    monkeypatch.setattr(cli, "uninstall", lambda game, log, keep_settings: seen.update(keep=keep_settings) or {})
    answer(monkeypatch, *([typed] if typed is not None else []))
    assert cli.main(["uninstall", "--game", str(tmp_path), *flags]) == 0
    assert seen["keep"] is keep


def _installed_here(monkeypatch, tmp_path, version):
    """Stub the install so the CLI sees an install of `version` in tmp_path and records what ran."""
    ran = []
    monkeypatch.setattr(cli, "install_state", lambda game: (version, "0.1.0"))
    monkeypatch.setattr(cli, "install", lambda game, inputs, **kw: ran.append(kw.get("force")))
    monkeypatch.setattr(cli, "TerminalView", Quiet)
    return ran, ["install", "--game", str(tmp_path), "--wo1", "a", "--wo1-x360", "w", "--x360", "b", "--psp", "c", "--x360-tool", "d", "--psp-tool", "e"]


def test_reinstalling_the_same_version_asks_in_a_terminal_and_needs_force_without_one(monkeypatch, tmp_path, capsys):
    # Bug: the same version is rebuilt without a word (a script or a stray Enter wipes the build), or a
    # "no" answer still installs.
    ran, argv = _installed_here(monkeypatch, tmp_path, "reinstall")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli.main(argv) == 2 and ran == [] and "--force" in capsys.readouterr().err
    assert cli.main([*argv, "--force"]) == 0 and ran == [True]
    answer(monkeypatch, "n")
    assert cli.main(argv) == 1 and ran == [True]
    answer(monkeypatch, "y")
    assert cli.main(argv) == 0 and len(ran) == 2


def test_the_menu_asks_again_after_a_bad_choice_and_ctrl_c_changes_nothing(monkeypatch, capsys):
    # Bug: a typo at the menu quits (or picks Install), or Ctrl-C leaves a traceback instead of a clean exit.
    ran = []
    monkeypatch.setattr(cli, "install", lambda *a, **kw: ran.append(1))
    answer(monkeypatch, "x", "9", "3")
    assert cli.main([]) == 0
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt))
    assert cli.main([]) == 1 and ran == [] and "Nothing was changed" in capsys.readouterr().err


def test_a_missing_program_pair_names_both_options_and_a_file_or_a_decrypted_disc_copy_covers_it(monkeypatch, tmp_path, capsys):
    # Bug: --x360-file is ignored (the program is still demanded), or the error names only one way.
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    discs = ["install", "--game", str(tmp_path), "--wo1", "a", "--wo1-x360", "w", "--x360", "b", "--psp", "c"]
    assert cli.main(discs) == 2
    error = capsys.readouterr().err
    assert "--x360-tool or --wo1-x360-file and --x360-file" in error and "--psp-tool or --psp-file" in error
    assert cli.main([*discs, "--wo1-x360-file", "r", "--x360-file", "p", "--psp-file", "q"]) != 2 or (
        "missing" not in capsys.readouterr().err)


def test_a_program_whose_platform_disc_image_holds_the_decrypted_file_is_neither_required_nor_asked_for(monkeypatch, tmp_path, capsys):
    # Bug: the command demands (or prompts for) a program the disc image makes unnecessary.
    monkeypatch.setattr(cli, "resolve_program", lambda disc, values: (disc, b"program") if disc != "psp" else ("psp-tool", "no"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli.main(["install", "--game", str(tmp_path), "--wo1", "a", "--wo1-x360", "w", "--x360", "b", "--psp", "c"]) == 2
    error = capsys.readouterr().err
    assert "--psp-tool or --psp-file" in error and "--x360-tool" not in error


def _released_version() -> str:
    import tomllib
    return tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["version"]


@pytest.mark.parametrize("flag", ["--version", "-v"])
def test_the_version_flag_prints_the_released_version(flag, capsys):
    # Bug: the flag is missing, takes a subcommand first, or prints a version other than the release's.
    with pytest.raises(SystemExit) as exit:
        cli.main([flag])
    assert exit.value.code == 0 and capsys.readouterr().out.strip() == f"wozmod {_released_version()}"

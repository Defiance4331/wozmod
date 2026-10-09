"""Public program-input checks using small authored files and external tools."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from wozmod.donors.programs import ProgramError, ProgramIdentity, prepare_program, verify_program


PAYLOAD = b"an authored test program"
ENCRYPTED = b"an authored encrypted input"


def _identity(platform: str = "x360", *, size: int | None = None) -> ProgramIdentity:
    return ProgramIdentity(
        "Test program", len(PAYLOAD) if size is None else size,
        hashlib.sha256(PAYLOAD).hexdigest(), platform,
    )


def _tool(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "tool argument.py"
    path.write_text("import sys\nfrom pathlib import Path\n" + body, encoding="utf-8")
    return [sys.executable, str(path)]


def test_verify_program_rejects_wrong_size_even_when_hash_matches(tmp_path: Path) -> None:
    source = tmp_path / "program"
    source.write_bytes(PAYLOAD)

    with pytest.raises(ProgramError, match="size"):
        verify_program(source, _identity(size=len(PAYLOAD) - 1))


def test_decrypted_route_rejects_same_size_wrong_hash(tmp_path: Path) -> None:
    source = tmp_path / "supplied program"
    source.write_bytes(PAYLOAD)
    identity = _identity()
    assert prepare_program(identity, tmp_path / "scratch", decrypted_file=source) == source

    wrong = b"X" + PAYLOAD[1:]
    source.write_bytes(wrong)
    with pytest.raises(ProgramError, match="SHA-256"):
        prepare_program(identity, tmp_path / "scratch", decrypted_file=source)
    assert source.read_bytes() == wrong


@pytest.mark.parametrize(
    ("recipe", "platform", "argument_check", "suffix"),
    [
        ("xex2ool", "x360", "assert args[0] == 'basefile' and args[2] == '-o'\nsource, output = Path(args[1]), Path(args[3])", 0),
        ("xextool", "x360", "assert args[0] == '-b'\noutput, source = Path(args[1]), Path(args[2])", 32768),
        ("pspdecrypt", "psp", "assert args[0] == '-o'\noutput, source = Path(args[1]), Path(args[2])", 0),
    ],
)
def test_tool_recipes_pass_copied_input_and_explicit_output(
    tmp_path: Path, recipe: str, platform: str, argument_check: str, suffix: int,
) -> None:
    original = tmp_path / "encrypted source ; with spaces"
    original.write_bytes(ENCRYPTED)
    tool = _tool(
        tmp_path,
        "original = Path(sys.argv[1])\nargs = sys.argv[2:]\n" + argument_check + "\n"
        "assert source.resolve() != original.resolve()\n"
        "assert source.read_bytes() == original.read_bytes()\n"
        "assert source.parent == output.parent == Path.cwd()\n"
        "assert not output.exists()\n"
        "source.write_bytes(b'changed scratch input')\n"
        f"output.write_bytes({PAYLOAD!r} + bytes({suffix}))\n",
    )
    result = prepare_program(
        _identity(platform), tmp_path / "scratch space", encrypted_file=original,
        tool=[*tool, str(original)], recipe=recipe,
    )

    assert result.is_relative_to(tmp_path / "scratch space")
    assert result.read_bytes() == PAYLOAD
    assert original.read_bytes() == ENCRYPTED


def test_zero_exit_without_new_output_cannot_reuse_previous_result(tmp_path: Path) -> None:
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    tool = _tool(tmp_path, f"Path(sys.argv[-1]).write_bytes({PAYLOAD!r})\n")
    first = prepare_program(
        _identity(), tmp_path / "scratch", encrypted_file=original,
        tool=tool, recipe="xex2ool",
    )
    assert first.read_bytes() == PAYLOAD
    tool = _tool(tmp_path, "pass\n")

    with pytest.raises(ProgramError, match="create its output"):
        prepare_program(
            _identity(), tmp_path / "scratch", encrypted_file=original,
            tool=tool, recipe="xex2ool",
        )
    assert first.read_bytes() == PAYLOAD


def test_nonzero_exit_rejects_matching_output(tmp_path: Path) -> None:
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    tool = _tool(
        tmp_path, f"Path(sys.argv[-1]).write_bytes({PAYLOAD!r})\nraise SystemExit(7)\n",
    )

    with pytest.raises(ProgramError, match="complete successfully"):
        prepare_program(
            _identity(), tmp_path / "scratch", encrypted_file=original,
            tool=tool, recipe="xex2ool",
        )


def test_tool_route_rejects_same_size_wrong_hash(tmp_path: Path) -> None:
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    wrong = b"X" + PAYLOAD[1:]
    tool = _tool(tmp_path, f"Path(sys.argv[-1]).write_bytes({wrong!r})\n")

    with pytest.raises(ProgramError, match="SHA-256"):
        prepare_program(
            _identity(), tmp_path / "scratch", encrypted_file=original,
            tool=tool, recipe="xex2ool",
        )


def test_xextool_rejects_nonzero_trailer_on_matching_prefix(tmp_path: Path) -> None:
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    tool = _tool(
        tmp_path,
        f"Path(sys.argv[2]).write_bytes({PAYLOAD!r} + bytes(32767) + b'X')\n",
    )

    with pytest.raises(ProgramError, match="padding"):
        prepare_program(
            _identity(), tmp_path / "scratch", encrypted_file=original,
            tool=tool, recipe="xextool",
        )


@pytest.mark.parametrize("padding_size", [0, 32767, 32769])
def test_xextool_rejects_wrong_padding_length_on_matching_prefix(
    tmp_path: Path, padding_size: int,
) -> None:
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    tool = _tool(
        tmp_path, f"Path(sys.argv[2]).write_bytes({PAYLOAD!r} + bytes({padding_size}))\n",
    )

    with pytest.raises(ProgramError, match="padded size"):
        prepare_program(
            _identity(), tmp_path / "scratch", encrypted_file=original,
            tool=tool, recipe="xextool",
        )


@pytest.mark.parametrize(
    ("platform", "recipe"), [("psp", "xex2ool"), ("psp", "xextool"), ("x360", "pspdecrypt")],
)
def test_program_platform_cannot_select_the_other_platform_recipe(
    tmp_path: Path, platform: str, recipe: str,
) -> None:
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    tool = _tool(
        tmp_path,
        "args = sys.argv[1:]\n"
        "output = Path(args[3] if args[0] == 'basefile' else args[1])\n"
        f"output.write_bytes({PAYLOAD!r} + (bytes(32768) if args[0] == '-b' else b''))\n",
    )

    with pytest.raises(ProgramError, match="platform"):
        prepare_program(
            _identity(platform), tmp_path / "scratch", encrypted_file=original,
            tool=tool, recipe=recipe,
        )


def test_ambiguous_inputs_cannot_silently_choose_decrypted_route(tmp_path: Path) -> None:
    supplied = tmp_path / "supplied program"
    supplied.write_bytes(PAYLOAD)
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    tool = _tool(tmp_path, f"Path(sys.argv[-1]).write_bytes({PAYLOAD!r})\n")

    with pytest.raises(ProgramError, match="either"):
        prepare_program(
            _identity(), tmp_path / "scratch", decrypted_file=supplied,
            encrypted_file=original, tool=tool, recipe="xex2ool",
        )


@pytest.mark.parametrize("link_method", ["symlink_to", "hardlink_to"])
def test_tool_output_link_cannot_alias_player_file(tmp_path: Path, link_method: str) -> None:
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    outside = tmp_path / "outside supplied program"
    outside.write_bytes(PAYLOAD)
    tool = _tool(
        tmp_path,
        f"Path(sys.argv[-1]).{link_method}(Path(sys.argv[1]))\n",
    )

    with pytest.raises(ProgramError, match="new regular file"):
        prepare_program(
            _identity(), tmp_path / "scratch", encrypted_file=original,
            tool=[*tool, str(outside)], recipe="xex2ool",
        )
    assert outside.read_bytes() == PAYLOAD


@pytest.mark.parametrize(("platform", "recipe"), [("x360", "xex2ool"), ("psp", "pspdecrypt")])
def test_tool_created_result_link_cannot_overwrite_player_file(
    tmp_path: Path, platform: str, recipe: str,
) -> None:
    original = tmp_path / "encrypted input"
    original.write_bytes(ENCRYPTED)
    outside = tmp_path / "protected player input"
    protected = b"independent protected input"
    outside.write_bytes(protected)
    result_name = "program.pe" if platform == "x360" else "eboot.prx"
    tool = _tool(
        tmp_path,
        "target = Path(sys.argv[1])\nargs = sys.argv[2:]\n"
        "output = Path(args[3] if args[0] == 'basefile' else args[1])\n"
        f"(output.parent / {result_name!r}).symlink_to(target)\n"
        f"output.write_bytes({PAYLOAD!r})\n",
    )

    with pytest.raises(ProgramError):
        prepare_program(
            _identity(platform), tmp_path / "scratch", encrypted_file=original,
            tool=[*tool, str(outside)], recipe=recipe,
        )
    assert outside.read_bytes() == protected

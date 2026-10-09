"""The repository holds our own sources only: no game files, byte dumps, game text or machine paths.

`.gitignore` is the first guard; these tests catch what slips past it, such as a
build output copied into `docs/`, or a translation row that still carries its
Japanese source string.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAYLOAD_SUFFIXES = {
    ".7z", ".avi", ".bdx", ".bik", ".bin", ".dds", ".dll", ".exe", ".flac", ".g1t", ".hdx", ".idx",
    ".img", ".iso", ".kovs", ".lnk", ".mkv", ".mp3", ".mp4", ".ogg", ".otf", ".png", ".ttf", ".wav",
    ".wmv", ".xex", ".zip",
}
DUMP_KEY_SUFFIXES = ("_hex", "_base64")
MACHINE_PATHS = ("/home/", "/Users/", "C:\\\\")
JAPANESE = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")


def _files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode()
    return [REPO / name for name in listed.split("\0") if name and (REPO / name).is_file()]


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_no_payload_files_are_tracked() -> None:
    payloads = [str(p.relative_to(REPO)) for p in _files() if p.suffix.lower() in PAYLOAD_SUFFIXES]

    assert payloads == []


def test_no_japanese_game_text_is_tracked() -> None:
    """Japanese is the game's own text; rows are keyed by hash, never by the stock string."""
    offending = [str(p.relative_to(REPO)) for p in _files() if JAPANESE.search(_text(p) or "")]

    assert offending == []


def test_json_holds_no_byte_dumps_or_machine_paths() -> None:
    offending = []
    for path in _files():
        if path.suffix != ".json":
            continue
        text = path.read_text(encoding="utf-8")
        if any(p in text for p in MACHINE_PATHS) or any(
            str(key).endswith(DUMP_KEY_SUFFIXES) for key in _keys(json.loads(text))
        ):
            offending.append(str(path.relative_to(REPO)))

    assert offending == []

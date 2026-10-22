"""The repository holds our own sources only: no game files, byte dumps, game text or machine paths.

`.gitignore` is the first guard; these tests catch what slips past it, such as a
build output copied into `docs/`, or a translation row that still carries its
Japanese source string.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# Our own work is source and data text; anything else (a .png, .tga, .xma, .dat) is a build output or game file.
OWN_SUFFIXES = {"", ".c", ".h", ".json", ".lock", ".md", ".py", ".rs", ".sh", ".spec", ".toml", ".txt", ".yml"}
# Game and media formats a release must not carry; the release legitimately holds .exe, .dll, .so, .png and .zip.
PAYLOAD_SUFFIXES = {
    ".7z", ".avi", ".bdx", ".bik", ".bin", ".dds", ".dll", ".exe", ".flac", ".g1t", ".hdx", ".idx",
    ".img", ".iso", ".kovs", ".lnk", ".mkv", ".mp3", ".mp4", ".ogg", ".otf", ".png", ".ttf", ".wav",
    ".wmv", ".xex", ".zip",
}
DUMP_KEY_SUFFIXES = ("_hex", "_base64")
MACHINE_PATHS = ("/home/", "/Users/", "C:\\\\")
# Kana, kanji, and the ideographic punctuation and fullwidth forms Japanese text carries.
JAPANESE = re.compile("[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff01-\uff9f\uffe0-\uffe6]")
# Our private notes and memory: tracked files name the behaviour, not the note that decided it.
PRIVATE = re.compile(r"\bADR[ -]?\d+|\bprivate/|\.agent-outputs|\bvault\b")
PRIVATE_GUARDS = {".gitignore", "tests/test_repository_hygiene.py", "tests/test_githooks.py", "tools/githooks/commit-msg"}


def _files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode()
    return [REPO / name for name in listed.split("\0") if name and (REPO / name).is_file()]


def _text(path: Path) -> str | None:
    """The file as text, JSON with its \\u escapes decoded; None when it is not UTF-8."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    if path.suffix == ".json":
        text = json.dumps(json.loads(text), ensure_ascii=False)
    return text


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_only_our_source_and_data_types_are_tracked() -> None:
    # Bug: an extracted texture, sound or archive (.tga, .xma, .dat) is committed under a type no list names.
    others = [str(p.relative_to(REPO)) for p in _files() if p.suffix.lower() not in OWN_SUFFIXES]

    assert others == []


def test_no_japanese_game_text_is_tracked() -> None:
    """Japanese is the game's own text; rows are keyed by hash, never by the stock string."""
    # Bug: the stock string is saved in the game's own Shift-JIS or UTF-16 (not UTF-8, so never searched),
    # or as JSON \\u escapes, and slips past a search of the raw UTF-8 text.
    offending = []
    for path in _files():
        text = _text(path)
        if text is None or JAPANESE.search(text):
            offending.append(str(path.relative_to(REPO)) + (" (not UTF-8)" if text is None else ""))

    assert offending == []


def test_no_tracked_file_cites_the_private_notes() -> None:
    # Bug: a comment cites a private ADR number or private/ path, which the public repo cannot resolve and
    # which once had to be scrubbed from history. The guards name the patterns on purpose.
    offending = [f"{p.relative_to(REPO)}:{n}" for p in _files() if p.relative_to(REPO).as_posix() not in PRIVATE_GUARDS
                 for n, line in enumerate((_text(p) or "").splitlines(), 1) if PRIVATE.search(line)]

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


def _wo1_pc_pointers(value, where: str = ""):
    """Where a JSON value names the Warriors Orochi PC donor, "wo1": a {"donor": "wo1"} source or a ["wo1", ...] ref."""
    if isinstance(value, dict):
        if value.get("donor") == "wo1":
            yield where
        for key, item in value.items():
            yield from _wo1_pc_pointers(item, f"{where}/{key}")
    elif isinstance(value, list):
        if value[:1] == ["wo1"]:
            yield where
        for index, item in enumerate(value):
            yield from _wo1_pc_pointers(item, f"{where}[{index}]")


def test_no_data_points_into_the_warriors_orochi_pc_disc() -> None:
    """Bug: a reference crop, text row or art piece still names the Warriors Orochi PC disc, which no install reads."""
    found = [f"{path.relative_to(REPO)}{where}" for path in _files() if path.suffix == ".json"
             for where in _wo1_pc_pointers(json.loads(path.read_text(encoding="utf-8")))]
    assert found == []


def _members(archive: zipfile.ZipFile, prefix: str = ""):
    """(name, bytes) of every member, nested zips opened in place."""
    for name in archive.namelist():
        data = archive.read(name)
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as inner:
                yield from _members(inner, f"{prefix}{name}!")
        else:
            yield prefix + name, data


@pytest.mark.skipif(not os.environ.get("WOZMOD_RELEASE"), reason="set WOZMOD_RELEASE to release zips (os.pathsep list)")
def test_release_zips_carry_no_game_files_or_game_text() -> None:
    # Bug: a build output with game content (a texture, voice bank or archive entry, or a data file holding
    # the stock Japanese) is packed into the release zip or into lettering.zip inside it.
    offending = []
    for release in os.environ["WOZMOD_RELEASE"].split(os.pathsep):
        with zipfile.ZipFile(release) as archive:
            for name, data in _members(archive):
                suffix = Path(name).suffix.lower()
                if suffix in PAYLOAD_SUFFIXES - {".dll", ".exe", ".png", ".zip"}:
                    offending.append(f"{release}!{name}")
                elif suffix in (".json", ".md", ".txt", ".ini"):  # our data; third-party .msg/.enc may hold Japanese
                    text = data.decode("utf-8", "replace")
                    if suffix == ".json":
                        text = json.dumps(json.loads(text), ensure_ascii=False)
                    if JAPANESE.search(text) or PRIVATE.search(text):
                        offending.append(f"{release}!{name}")

    assert offending == []


def _donor_strings(root: Path) -> set[str]:
    """Every string the games show, case and spacing folded: their ENS archives, the PSP pool, and the program
    strings the text table and the art data point at (a program's other bytes, such as shader code, are not text)."""
    from wozmod.formats.linkdata import parse_wo1_index
    from wozmod.formats.scripts import donor_text
    from wozmod.text.resolver import DonorResolver

    resolver, found = DonorResolver(root), set()

    def strings(data: bytes, decode):
        for match in re.finditer(rb"(?:(?<=\0)|^)[^\0]{2,}", data):
            try:
                text = decode(match.group())
            except ValueError:
                continue
            if text:
                found.add(" ".join(text.casefold().split()))

    for donor in ("wo1-x360", "wo2-x360"):
        stem = root / donor / "linkdata_ens"
        for ordinal in range(len(parse_wo1_index(stem.with_suffix(".idx").read_bytes(),
                                                 data_size=stem.with_suffix(".lnk").stat().st_size))):
            try:
                strings(resolver.entry(donor, ordinal), donor_text)
            except ValueError:
                continue
    found |= {" ".join(text.casefold().split()) for text in resolver.psp_pool() if text}
    table = json.loads((REPO / "data/text/table.json").read_text(encoding="utf-8"))
    pointers = [*_strings(table, refs=True),
                *(edit[2] for row in table["rows"] for edit in row.get("edits", []) if isinstance(edit[2], list)),
                *(ref for path in sorted((REPO / "data/art").glob("*.json"))
                  for ref in _strings(json.loads(path.read_text(encoding="utf-8")), refs=True))]
    found |= {" ".join(resolver.resolve(ref).casefold().split()) for ref in pointers if ref[1] == "pe"}
    return found


# Our own words that equal an official string and stay ours by decision, by SHA-256: the unused-slot label.
OWN_WORDS = {"231aedffacea10497c1851e05277da1baa518600f56ebd2123410bc90e7a0190"}
# Where text or lettering is stored: the data, the code that letters and resolves text, and test literals.
TEXT_CODE = ("data/", "src/wozmod/art/", "src/wozmod/fonts/", "src/wozmod/text/", "tests/")
FILE_NAME = re.compile(r"[\w-]+\.[A-Za-z0-9]{1,4}")


@pytest.mark.skipif(not (os.environ.get("WOZMOD_ART_WORDS") and os.environ.get("WOZMOD_DONORS")),
                    reason="set WOZMOD_ART_WORDS=1 and WOZMOD_DONORS (about a minute)")
def test_tracked_text_stores_no_official_english() -> None:
    # Bug: an official word or line is typed into the data, a font table's keys, the lettering code or a test (in
    # any case or spacing) instead of a pointer to its donor string, a letter map of its donor image or a hash.
    from wozmod.art import build, faces, vector

    official = _donor_strings(Path(os.environ["WOZMOD_DONORS"]))
    stored = [(f"{module.__name__} {name}", text) for module in (build, faces, vector)
              for name, value in vars(module).items() if name.isupper() for text in _strings(value)]
    for path in _files():
        where = path.relative_to(REPO).as_posix()
        if path.suffix == ".json" and where.startswith("data/"):
            stored += [(where, text) for text in _strings(json.loads(path.read_text(encoding="utf-8")))]
        elif path.suffix == ".py" and where.startswith(TEXT_CODE):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            stored += [(f"{where}:{node.lineno}", node.value) for node in ast.walk(tree)
                       if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    # A word: three letters or more with a capital (identifiers and style names are lower case); a bare file name
    # names a file, it is not text.
    offending = sorted({f"{where}: {hashlib.sha256(text.encode()).hexdigest()[:12]}" for where, text in stored
                        if sum(c.isalpha() for c in text) >= 3 and any(c.isupper() for c in text)
                        and not FILE_NAME.fullmatch(text)
                        and hashlib.sha256(text.encode()).hexdigest() not in OWN_WORDS
                        and " ".join(text.casefold().split()) in official})

    assert offending == []


def _strings(value, refs: bool = False):
    """Every string in value, or with refs every donor pointer (a "ref" list)."""
    if isinstance(value, str) and not refs:
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if refs and key == "ref" and isinstance(item, list):
                yield item
                continue
            if not refs:
                yield str(key)
            yield from _strings(item, refs)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item, refs)

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
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# Our own work is source and data text; anything else (a .png, .tga, .xma, .dat) is a build output or game file.
OWN_SUFFIXES = {"", ".c", ".comp", ".cu", ".h", ".json", ".lock", ".md", ".patch", ".py", ".rs", ".sh", ".spec", ".toml", ".txt",
                ".yml"}
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


TREE = pytest.mark.linux_only("checks the repository's own files, the same bytes whichever system runs the check, "
                              "listed by git, which the Windows test environment has no need for")


def _files() -> list[Path]:
    """The repository's files, as git lists them: tracked and untracked, ignored ones left out. An export of a tree
    (tools/verify.py runs there) has no .git and sits in the main checkout's ignored work/, where git lists nothing:
    there an empty repository lists the export's files, under the tree's own .gitignore."""
    listing = ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    if (REPO / ".git").exists():
        listed = subprocess.run(["git", *listing], cwd=REPO, capture_output=True, check=True).stdout.decode()
    else:
        env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        with tempfile.TemporaryDirectory() as empty:
            subprocess.run(["git", "init", "-q", "--bare", empty], env=env, capture_output=True, check=True)
            listed = subprocess.run(["git", f"--git-dir={empty}", f"--work-tree={REPO}", *listing], cwd=REPO, env=env,
                                    capture_output=True, check=True).stdout.decode()
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


@TREE
def test_only_our_source_and_data_types_are_tracked() -> None:
    # Bug: an extracted texture, sound or archive (.tga, .xma, .dat) is committed under a type no list names.
    others = [str(p.relative_to(REPO)) for p in _files() if p.suffix.lower() not in OWN_SUFFIXES]

    assert others == []


@TREE
def test_on_an_export_the_checks_read_its_files(tmp_path) -> None:
    """Bugs: on an export of a tree (tools/verify.py runs there: no .git, inside the main checkout's ignored work/),
    git lists no file, so every check here passes whatever the tree holds; or the listing takes in what the tree's
    .gitignore leaves out, such as the run's own temporary files in the export's work/."""
    import shutil
    import sys

    # without what git sets for its hooks (GIT_DIR): the commit hook runs this, and git init would reinitialise ours
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    git = lambda *arguments: subprocess.run(["git", *arguments], cwd=tmp_path, env=env, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / ".gitignore").write_text("work/\n", encoding="utf-8")
    folder = tmp_path / "work/verify/trees/TREE"
    (folder / "tests").mkdir(parents=True)
    shutil.copy(Path(__file__), folder / "tests" / Path(__file__).name)
    (folder / "stock.bin").write_bytes(b"\0")
    (folder / ".gitignore").write_text("work/\n", encoding="utf-8")
    (folder / "work/test-tmp").mkdir(parents=True)
    (folder / "work/test-tmp/fit.so").write_bytes(b"\0")  # what a test run leaves there
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-n0", "-p", "no:cacheprovider", "-k",
                          "only_our_source", "tests"], cwd=folder, env=env, capture_output=True, encoding="utf-8")
    assert (run.returncode, "stock.bin" in run.stdout, "fit.so" in run.stdout) == (1, True, False), run.stdout


@TREE
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


@TREE
def test_no_tracked_file_cites_the_private_notes() -> None:
    # Bug: a comment cites a private ADR number or private/ path, which the public repo cannot resolve and
    # which once had to be scrubbed from history. The guards name the patterns on purpose.
    offending = [f"{p.relative_to(REPO)}:{n}" for p in _files() if p.relative_to(REPO).as_posix() not in PRIVATE_GUARDS
                 for n, line in enumerate((_text(p) or "").splitlines(), 1) if PRIVATE.search(line)]

    assert offending == []


TEXT_CALLS = {"read_text": 0, "write_text": 1}  # Path method: the position its encoding argument may take
PROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}


def _unencoded(tree: ast.AST) -> list[int]:
    """Lines of calls that take the locale's text encoding: Path.read_text/write_text, the builtin open in text mode, a
    subprocess call with text=True or universal_newlines=True, each with no encoding named."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        named = {k.arg for k in node.keywords}
        if "encoding" in named:
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in TEXT_CALLS and len(node.args) <= TEXT_CALLS[func.attr]:
            found.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id == "open" and len(node.args) < 4:
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            if not (isinstance(mode, ast.Constant) and "b" in str(mode.value)) and (mode is None or isinstance(mode, ast.Constant)):
                found.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr in PROCESS_CALLS and any(
                k.arg in ("text", "universal_newlines") and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in node.keywords):
            found.append(node.lineno)
    return found


@TREE
def test_text_files_and_program_output_name_their_encoding() -> None:
    # Bug: a text read, write or program output with no encoding takes the locale's: cp1252 on Windows, where UTF-8
    # data (our translations, recipes, JSON) fails to decode or is written garbled, while Linux, UTF-8 everywhere,
    # stays green. 247 such calls in tests/ and tools/ broke the suite on Windows Python before this check.
    unencoded = []
    for path in _files():
        if path.suffix == ".py" and path.relative_to(REPO).parts[0] in ("src", "tests", "tools", "packaging"):
            unencoded += [f"{path.relative_to(REPO)}:{line}" for line in _unencoded(ast.parse(path.read_bytes()))]
    assert unencoded == [], "name the encoding (encoding=\"utf-8\" for our files)"


def test_the_encoding_check_finds_each_kind_of_unencoded_call() -> None:
    # Bug: the check above misses a kind of call (a positional mode, a subprocess with text=True), so it passes while
    # such calls come back.
    flagged = ("p.read_text()", "p.write_text(s)", "open(f)", "open(f, 'w')", "open(f, mode='r')",
               "subprocess.run(a, text=True)", "subprocess.check_output(a, universal_newlines=True)")
    allowed = ("p.read_text(encoding='utf-8')", "p.read_text('utf-8')", "p.write_text(s, 'utf-8')", "open(f, 'rb')",
               "open(f, mode='wb')", "open(f, encoding='cp932')", "subprocess.run(a, text=True, encoding='utf-8')",
               "subprocess.run(a, capture_output=True)", "p.read_bytes()")
    assert [code for code in flagged if not _unencoded(ast.parse(code))] == []
    assert [code for code in allowed if _unencoded(ast.parse(code))] == []


@TREE
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


@TREE
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


def _pinned_speech_file(name: str, data: bytes) -> bool:
    """True for a release member under _internal/speech/ that is exactly the model file voices/phones.py pins."""
    from wozmod.voices.phones import MODEL

    _, _, inside = name.partition("/_internal/speech/")
    return bool(inside) and MODEL.get(inside) == (len(data), hashlib.sha256(data).hexdigest())


@pytest.mark.skipif(not os.environ.get("WOZMOD_RELEASE"), reason="set WOZMOD_RELEASE to release zips (os.pathsep list)")
def test_release_zips_carry_no_game_files_or_game_text() -> None:
    # Bug: a build output with game content (a texture, voice bank or archive entry, or a data file holding
    # the stock Japanese) is packed into the release zip or into lettering.zip inside it.
    offending = []
    for release in os.environ["WOZMOD_RELEASE"].split(os.pathsep):
        with zipfile.ZipFile(release) as archive:
            for name, data in _members(archive):
                suffix = Path(name).suffix.lower()
                if _pinned_speech_file(name, data):
                    continue  # PocketSphinx's own model, byte for byte as voices/phones.py pins it
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
@TREE
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

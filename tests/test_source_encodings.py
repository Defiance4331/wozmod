"""Every text file the program reads or writes names its encoding."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _text_mode(call: ast.Call) -> bool:
    """True for read_text/write_text, builtin open, Path.open with a text mode string, and a program started with
    text=True (or universal_newlines=True), whose output is decoded."""
    name = call.func
    if any(k.arg in ("text", "universal_newlines") and isinstance(k.value, ast.Constant) and k.value.value is True
           for k in call.keywords):
        return True
    if isinstance(name, ast.Attribute) and name.attr in ("read_text", "write_text"):
        return True
    modes = [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    modes += [k.value.value for k in call.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
    if isinstance(name, ast.Name) and name.id == "open":
        return not any("b" in m for m in modes)
    # Image.open(path) and friends take no mode string; only Path.open("r") style calls do.
    return isinstance(name, ast.Attribute) and name.attr == "open" and bool(modes) and not any("b" in m for m in modes)


def missing_encodings() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _text_mode(node) and not any(k.arg == "encoding" for k in node.keywords):
                found.append(f"{path.relative_to(SRC)}:{node.lineno}")
    return found


def test_text_files_name_their_encoding() -> None:
    # Bug: without encoding= Windows reads and writes in its locale's code page (cp1252, or cp932 on a Japanese
    # Windows), so JSON or C source with non-ASCII text (or a log line) crashes the install there while it passes on
    # Linux, or a program's output parses differently and the same step key holds other bytes on that machine.
    assert missing_encodings() == []

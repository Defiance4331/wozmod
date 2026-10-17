"""Resolve hashed rows and build the runtime text table."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import TYPE_CHECKING

from wozmod.progress import report
from wozmod.runtime.english_runtime import (
    SURROGATES, encode_english, file_offset, pe_layout, table_blob,
)

if TYPE_CHECKING:
    from .resolver import DonorResolver

NAME_TABLE = 0x6F6368
NAME_PARTS = 890
SOURCES = frozenset({"wo1-x360", "wo2-x360", "wo2-ps2", "wo2-psp", "edited", "authored"})
FORMAT_SPEC = re.compile(rb"%[-+#0-9.]*[a-zA-Z]")
ENGLISH_FORMAT_SPEC = re.compile(rb"%%|%s|%d|%02d")
PRINTF_FORM = re.compile(rb"%[-+ #0-9.*]*(?:I64|ll|[hl])?[a-zA-Z]?")
IBM_KANJI = {
    character: code.to_bytes(2, "big")
    for code in range(0xFA5C, 0xFC4C)
    if len(character := code.to_bytes(2, "big").decode("cp932", "replace")) == 1
    and character != "\ufffd"
}


class TableBuildError(ValueError):
    """A translation row cannot safely enter the runtime table."""


# Icon codes the stock draw's jump table (0x549F48) handles; F0 48 takes two argument bytes.
DRAWABLE_ICONS = frozenset(range(0x40, 0x5A)) - {0x49, 0x4B, 0x56}


def _undrawable_icon(english: bytes) -> bool:
    at = 0
    while at < len(english):
        if english[at] == 0xF0:
            code = english[at + 1] if at + 1 < len(english) else None
            if code not in DRAWABLE_ICONS:
                return True
            at += 4 if code == 0x48 else 2
        else:
            at += 2 if 0x81 <= english[at] <= 0x9F or english[at] >= 0xE0 else 1
    return False


def cp932_forms(text: str) -> tuple[bytes, ...]:
    """Canonical CP932 followed by IBM codes, preserving baseline order."""
    return tuple(dict.fromkeys((
        text.encode("cp932"),
        b"".join(IBM_KANJI.get(character) or character.encode("cp932") for character in text),
    )))


def _row_key(row: dict) -> str:
    return row.get("key") or hashlib.sha256(row["ja"].encode("cp932")).hexdigest()


def _draw_checks(stock: bytes, text: str, english: bytes, key: str) -> None:
    problems = []
    if any(character >= "\x80" and len(character.translate(SURROGATES).encode("cp932")) != 2
           for character in text):
        problems.append("high byte")
    if set(text) & set("\\`{|}\t") or re.search(r"\[(?!\d\d)", text):
        problems.append("control")
    if chr(0xFF05) in stock.decode("cp932") and b"%%" not in english:
        problems.append("literal percent")
    if stock[:1] == b"\xf0" and english[:2] != stock[:2]:
        problems.append("shape")
    if _undrawable_icon(english):
        problems.append("icon")
    if stock[:1].isdigit() and (b"%s" in stock or b"[1" in stock) and english[:1] != stock[:1]:
        problems.append("objective count")
    if problems:
        raise TableBuildError(f"row {key}: invalid renderer text ({', '.join(problems)})")


def table_index(rows: list[dict]) -> dict[bytes, tuple[bytes, dict]]:
    """Runtime keys mapped to encoded English and their originating in-memory row."""
    index: dict[bytes, tuple[bytes, dict]] = {}

    def add(stock: bytes, english: bytes, row: dict) -> None:
        specs = [FORMAT_SPEC.findall(text.replace(b"%%", b"")) for text in (stock, english)]
        if specs[1] != specs[0][:len(specs[1])]:
            raise TableBuildError(f"row {_row_key(row)}: format arguments differ")
        if stock and stock != english:
            index[stock] = (english, row)

    for row in rows:
        key = _row_key(row)
        try:
            english = encode_english(row["en"])
            forms = cp932_forms(row["ja"])
        except UnicodeEncodeError as error:
            raise TableBuildError(f"row {key}: unencodable text") from error
        _draw_checks(forms[0], row["en"], english, key)
        at = english.find(b"%")
        while at != -1:
            spec = ENGLISH_FORMAT_SPEC.match(english, at)
            if spec is None:
                form = PRINTF_FORM.match(english, at)[0].decode("ascii")
                raise TableBuildError(f"row {key}: unsupported English printf form {form!r}")
            at = english.find(b"%", spec.end())
        for stock in forms:
            add(stock, english, row)
            if stock[:1].isdigit() and english[:1] == stock[:1] and (b"%s" in stock or b"[1" in stock):
                add(stock[1:], english[1:], row)
    return index


def table_pairs(rows: list[dict]) -> dict[bytes, bytes]:
    """Build lookup pairs from transient rows containing stock and resolved English."""
    return {stock: english for stock, (english, _) in table_index(rows).items()}


def name_parts(stock: bytes, pairs: dict[bytes, bytes], overrides: list[dict]) -> list[bytes | None]:
    """Resolve all name slots, retaining per-index overrides and exact stock bytes."""
    layout = pe_layout(stock)
    by_index = {entry["index"]: encode_english(entry["en"]) for entry in overrides}
    names = []
    for index in range(NAME_PARTS):
        pointer = file_offset(layout, NAME_TABLE + 8 * index)
        at = file_offset(layout, struct.unpack_from("<I", stock, pointer)[0])
        names.append(by_index.get(index) or pairs.get(stock[at:stock.index(b"\0", at)]))
    return names


def _load_data(path: Path) -> dict:
    data = json.loads((path / "table.json" if path.is_dir() else path).read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("rows"), list) or not isinstance(data.get("name_parts"), list):
        raise TableBuildError("unsupported text data schema")
    keys = set()
    for row in data["rows"]:
        key = row.get("key", "")
        if not isinstance(key, str) or not re.fullmatch("[0-9a-f]{64}", key):
            raise TableBuildError("invalid stock string hash")
        if key in keys:
            raise TableBuildError(f"duplicate row {key}")
        keys.add(key)
        if row.get("source") not in SOURCES or row.get("todo") not in (None, "wo2-psp"):
            raise TableBuildError(f"row {key}: invalid source")
    indexes = set()
    for row in data["name_parts"]:
        index = row.get("index")
        if type(index) is not int or not 0 <= index < NAME_PARTS or index in indexes:
            raise TableBuildError(f"invalid or duplicate name-part index {index!r}")
        indexes.add(index)
        if row.get("source") not in SOURCES:
            raise TableBuildError(f"name part {index}: invalid source")
    return data


def build_table(stock: Path, donors: DonorResolver, data: Path, output: Path) -> dict:
    """Build table.bin from player inputs and version-1 hash/pointer data."""
    from .stock import resolve_stock_ref, stock_strings

    payload = _load_data(data)
    catalog = stock_strings(stock)
    rows = []
    for number, row in enumerate(payload["rows"]):
        if number % 20 == 0:
            report("text", "building the text table", 0.9 * number / len(payload["rows"]), (number, len(payload["rows"])))
        stock_bytes = catalog.get(row["key"])
        if stock_bytes is None and "key_ref" in row:
            stock_bytes = resolve_stock_ref(stock, row["key_ref"], row["key"])
        if stock_bytes is None:
            raise TableBuildError(f"stock string absent for row {row['key']}")
        rows.append({**row, "ja": stock_bytes.decode("cp932"), "en": donors.resolve_row(row)})
    overrides = [{**row, "en": donors.resolve_row(row)} for row in payload["name_parts"]]
    pairs = table_pairs(rows)
    names = name_parts((stock / "OROCHI_Z.exe").read_bytes(), pairs, overrides)
    blob = table_blob(pairs, names)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(blob)
    return {
        "rows": len(rows), "pairs": len(pairs), "name_parts": sum(name is not None for name in names),
        "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
    }

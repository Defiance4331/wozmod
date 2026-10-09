"""Resolve v1 donor pointers without retaining donor text.

Pointers are positional arrays, with zero-based indices:
  [donor, "ens", entry, view, *indices]
  ["wo1", "exe", byte_offset], ["x360", "pe", byte_offset],
  or ["psp", "prx", string_slot]
Donors are wo1, x360 and psp. ENS names linkdata_ens.lnk/.idx.
Views: notices/dialog (slot), mesc (block, slot), quotes (table, slot),
event (line id), captions (list of cue slots, whitespace joined), cstr
(byte offset), slice (byte offset, byte length). Offsets address decoded
archive entries. A slice can select a name part inside a donor record.
Edits are ascending [start, end, replacement] character spans in the
unmodified resolved string; only replacement text is authored data.
Program strings may include CP932 private glyphs. Official prompt controls
use the same spans under "controls", replacing text with numeric codepoints.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from wozmod.formats.linkdata import wo1_entries
from wozmod.formats.scripts import (
    caption_cues, donor_text, event_lines, mesc_blocks, parse_script,
    quote_tables, quote_text,
)
from wozmod.donors.programs import X360_PROGRAM, verify_program


def program_text(raw: bytes) -> str:
    """Decode donor letters and the programs' two-byte private prompt glyphs."""
    parts, at = [], 0
    while at < len(raw):
        if 0xF0 <= raw[at] <= 0xF9:
            glyph = raw[at:at + 2].decode("cp932")
            if len(glyph) != 1 or not 0xE000 <= ord(glyph) <= 0xE757:
                raise ValueError("Invalid program prompt glyph")
            parts.append(glyph)
            at += 2
        else:
            text = donor_text(raw[at:at + 1])
            if text is None:
                raise ValueError("Invalid donor program text")
            parts.append(text)
            at += 1
    return "".join(parts)


class DonorResolver:
    def __init__(self, root: Path, *, wo1_program: Path | None = None):
        self.root = Path(root)
        self.wo1_program = Path(wo1_program) if wo1_program is not None else self.root / "wo1/OROCHI.exe"

    @cache
    def entry(self, donor: str, ordinal: int) -> bytes:
        folder = {"wo1": "wo1", "x360": "wo2-x360"}.get(donor)
        if folder is None or not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError("Invalid ENS donor or entry")
        base = self.root / folder / "linkdata_ens"
        return wo1_entries(base.with_suffix(".lnk"), base.with_suffix(".idx"), [ordinal])[ordinal]

    @cache
    def program(self, donor: str) -> bytes:
        if donor == "wo1":
            from hashlib import sha256
            data = self.wo1_program.read_bytes()
            if len(data) != 4310048 or sha256(data).hexdigest() != "387c45a2631ba0ee72830fb9ddb90dc813fa19c7764eb932d5f65e234fe28b2b":
                raise ValueError("Invalid WO1 disc program")
            return data
        if donor != "x360":
            raise ValueError("Invalid PE donor")
        path = self.root / "wo2-x360/default.pe"
        verify_program(path, X360_PROGRAM)
        return path.read_bytes()

    @cache
    def psp_pool(self) -> list[str | None]:
        from .psp_exe import eboot_strings
        return eboot_strings(self.root / "wo2-psp/eboot.prx")

    @cache
    def view(self, donor: str, ordinal: int, kind: str):
        data = self.entry(donor, ordinal)
        order = ">" if donor == "x360" else "<"
        if kind in ("notices", "dialog"):
            return getattr(parse_script(data, order), kind).records
        if kind == "mesc":
            return mesc_blocks(data)
        if kind == "quotes":
            return quote_tables(data, order)
        if kind == "event":
            return event_lines(data, order)
        if kind == "captions":
            return caption_cues(data)
        raise ValueError("Invalid ENS view")

    def resolve(self, ref: list) -> str:
        if not isinstance(ref, list) or len(ref) < 3:
            raise ValueError("Invalid donor pointer")
        donor, file, *parts = ref
        if any(not isinstance(i, int) or i < 0 for i in parts[:1]):
            raise ValueError("Invalid donor index")
        if file == "prx" and donor == "psp" and len(parts) == 1:
            text = self.psp_pool()[parts[0]]
        elif (file, donor) in (("pe", "x360"), ("exe", "wo1")) and len(parts) == 1:
            data = self.program(donor)
            at = parts[0]
            if at >= len(data):
                raise ValueError("PE pointer outside program")
            text = program_text(data[at:data.index(b"\0", at)])
        elif file == "ens" and len(parts) >= 2:
            ordinal, kind, *indices = parts
            data = self.entry(donor, ordinal)
            if kind in ("cstr", "slice"):
                if len(indices) != (1 if kind == "cstr" else 2) or any(
                    not isinstance(i, int) or i < 0 for i in indices
                ):
                    raise ValueError("Invalid string span")
                at = indices[0]
                end = data.index(b"\0", at) if kind == "cstr" else at + indices[1]
                if not at <= end <= len(data):
                    raise ValueError("String span outside entry")
                text = donor_text(data[at:end])
            else:
                flat = indices[0] if kind == "captions" and len(indices) == 1 and isinstance(indices[0], list) else indices
                if any(type(i) is not int or i < 0 for i in flat):
                    raise ValueError("Invalid view indices")
                view = self.view(donor, ordinal, kind)
                if kind in ("notices", "dialog") and len(indices) == 1:
                    raw = view[indices[0]].text_bytes
                elif kind == "mesc" and len(indices) == 2:
                    raw = view[indices[0]][indices[1]]
                elif kind == "quotes" and len(indices) == 2:
                    raw = quote_text(view[indices[0]][indices[1]])
                elif kind == "event" and len(indices) == 1:
                    raw = view[indices[0]]
                elif kind == "captions" and len(indices) == 1 and isinstance(indices[0], list):
                    raw = b" ".join(word for slot in indices[0] for word in view[slot][2].split())
                else:
                    raise ValueError("Invalid view indices")
                text = donor_text(raw)
        else:
            raise ValueError("Invalid donor file")
        if text is None:
            raise ValueError("Pointer does not resolve to donor English")
        return text

    def resolve_row(self, row: dict) -> str:
        if "en" in row:
            if row.get("source") != "authored" or "ref" in row or "edits" in row:
                raise ValueError("Only authored rows hold English")
            return row["en"]
        text = self.resolve(row["ref"])
        edits = row.get("edits", [])
        if bool(edits) != (row.get("source") == "edited"):
            raise ValueError("Only edited rows hold edit recipes")
        if "controls" in row:
            if edits or row.get("source") not in ("wo1-pc", "wo2-x360"):
                raise ValueError("Invalid prompt controls")
            edits = []
            for start, stop, codes in row["controls"]:
                if any(code != 32 and not 0xE000 <= code <= 0xE016 for code in codes):
                    raise ValueError("Invalid prompt control code")
                edits.append([start, stop, "".join(map(chr, codes))])
        end, chunks = 0, []
        for start, stop, replacement in edits:
            if not end <= start <= stop <= len(text):
                raise ValueError("Edit spans overlap or exceed donor string")
            chunks.extend((text[end:start], replacement))
            end = stop
        return "".join((*chunks, text[end:]))


def layout_sample(donors: DonorResolver) -> str:
    """Resolve the two name parts used by runtime layout diagnostics."""
    return " ".join(donors.resolve(["x360", "pe", at]) for at in (161192, 174664))


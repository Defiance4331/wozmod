#!/usr/bin/env python3
"""Disassemble the stock OROCHI_Z.exe, and find what refers to an address: the one tracked helper, instead of a
per-task dis.py.

    uv run python tools/disasm.py range 0x5974F0 0x597600 [--patched]   # LO to HI; --patched applies the runtime's
                                                                         # English code edits (FONT_ROUTE, CODE_EDITS)
    uv run python tools/disasm.py refs 0x5974F0                          # calls and jumps to it, and its 32-bit value

The exe is WOZMOD_GAME's from `[tests]` in work/harness.toml, or --exe.
"""

from __future__ import annotations

import argparse
import struct
import sys
import tomllib
from pathlib import Path

import capstone

import checkout
from wozmod.formats import pe

CODE = 0x20  # IMAGE_SCN_CNT_CODE
REL32 = {b"\xe8": 5, b"\xe9": 5, **{bytes([0x0F, op]): 6 for op in range(0x80, 0x90)}}  # call, jmp, jcc: length


def stock_exe() -> Path:
    tests = tomllib.loads(checkout.config().read_text())["tests"]
    return Path(tests["WOZMOD_GAME"]) / "OROCHI_Z.exe"


def code_sections(image: bytes, layout: dict) -> list[tuple[int, bytes]]:
    """(VA, raw bytes) of each code section; pe.sections leaves out the characteristics, read here."""
    table = struct.unpack_from("<I", image, 0x3C)[0] + 24 + struct.unpack_from("<H", image, struct.unpack_from(
        "<I", image, 0x3C)[0] + 20)[0]
    found = []
    for i, s in enumerate(layout["sections"]):
        if struct.unpack_from("<I", image, table + 40 * i + 36)[0] & CODE:
            found.append((layout["image_base"] + s["rva"], image[s["raw"]:s["raw"] + s["raw_size"]]))
    return found


def patched(image: bytes, layout: dict) -> bytes:
    """The image with the English mode's code edits applied, each over the stock bytes it expects."""
    from wozmod.runtime.runtime_edits import CODE_EDITS, FONT_ROUTE
    out = bytearray(image)
    for va, old, new in FONT_ROUTE + CODE_EDITS:
        at = pe.file_offset(layout, va)
        if out[at:at + len(old)] != old:
            raise ValueError(f"edit at {va:#x}: the exe does not hold its stock bytes")
        out[at:at + len(new)] = new
    return bytes(out)


def show_range(image: bytes, layout: dict, lo: int, hi: int) -> list[str]:
    start = pe.file_offset(layout, lo)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    return [f"{i.address:06x}  {i.bytes.hex():<16} {i.mnemonic} {i.op_str}"
            for i in md.disasm(image[start:start + hi - lo], lo)]


def refs(image: bytes, layout: dict, target: int) -> list[str]:
    """Every call, jmp or jcc rel32 to target, then every place its 32-bit value appears (an immediate, displacement or
    pointer in any section). An immediate's instruction is decoded from the nearest start that ends just past it, so
    read it as a guess."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    found = []
    for base, data in code_sections(image, layout):
        for opcode, size in REL32.items():
            at = data.find(opcode)
            while at != -1:
                if at + size <= len(data):
                    va = base + at
                    if (va + size + struct.unpack_from("<i", data, at + len(opcode))[0]) & 0xFFFFFFFF == target:
                        i = next(md.disasm(data[at:at + size], va))
                        found.append((va, f"{va:06x}  {i.mnemonic} {i.op_str}"))
                at = data.find(opcode, at + 1)
    value, code = struct.pack("<I", target), code_sections(image, layout)
    for s in layout["sections"]:
        data, base = image[s["raw"]:s["raw"] + s["raw_size"]], layout["image_base"] + s["rva"]
        at = data.find(value)
        while at != -1:
            va, line = base + at, f"{base + at:06x}  value in {s['name'].decode()}"
            if any(b <= va < b + len(d) for b, d in code):
                for back in range(1, 8):
                    i = next(md.disasm(data[at - back:at + 8], va - back), None)
                    if i and va + 4 <= i.address + i.size <= va + 5:
                        line = f"{i.address:06x}  {i.mnemonic} {i.op_str}  (guessed start)"
                        break
            found.append((va, line))
            at = data.find(value, at + 1)
    return [line for _, line in sorted(found)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, help="another OROCHI_Z.exe")
    commands = parser.add_subparsers(dest="action", required=True)
    one = commands.add_parser("range")
    one.add_argument("lo", type=lambda v: int(v, 16))
    one.add_argument("hi", type=lambda v: int(v, 16))
    one.add_argument("--patched", action="store_true")
    commands.add_parser("refs").add_argument("va", type=lambda v: int(v, 16))
    args = parser.parse_args(argv)
    try:
        image = (args.exe or stock_exe()).read_bytes()
        layout = pe.layout(image)
        if args.action == "range":
            lines = show_range(patched(image, layout) if args.patched else image, layout, args.lo, args.hi)
        else:
            lines = refs(image, layout, args.va)
    except (OSError, ValueError, KeyError) as exc:
        print(f"disasm: {exc}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

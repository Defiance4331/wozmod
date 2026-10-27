"""The true frame rate catalogue: data/true_rate.toml checked against the stock executable and packed for the runtime.

Each site names an instruction in OROCHI_Z.exe by VA and its stock bytes, says why it steps once per tick, and how
the runtime makes it step by real time (runtime/true_rate.h): an operand pointed at a transformed copy of its
constant, a patch run in a thunk, or a gate that runs the stock bytes only on ticks that complete a 60 Hz period.
Replacement code is written as Intel-syntax assembly with symbols: {step} (f32 60/N), {step_d} (f64 60/N), {tick60}
(u32, 1 on a 60 Hz tick), {n}, {once} (u32) and {nf} (f32) for the call's window, [{character}] (call:
tr_character(float lod), which sets that window for a character's update), {rate} (u32 N), {frame60} (u32, 1 on a
pass whose real time completes a 60 Hz period, or a stand-in that takes one of its further periods), {lod} (f32, the
character call's far factor 1 or 6), [{frame}] (call: tr_frame_step(), a frame the catch-up skipped), {limit} (u32,
the present limiter's passes a second), {frame_dt} (f32, the seconds the ticks since the last present cover: what a
step taken once a drawn frame advances by), {slot:NAME} (a transformed constant) and {call:VA} / {jmp:VA} (a branch
out)."""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from wozmod.formats import pe

CATALOGUE = Path(__file__).resolve().parents[3] / "data/true_rate"  # one TOML file per subsystem
ASSEMBLER = "i686-w64-mingw32-as"
KINDS = ("operand", "patch", "gate")
CLASSES = ("a", "b", "c", "d", "e")
TRANSFORMS = {"scale": 1, "pow": 2, "ease": 3, "inverse": 4, "decay_sum": 5}
SYMBOLS = {"step": 0, "tick60": 1, "step_d": 2, "n": 3, "once": 4, "nf": 5, "character": 6, "rate": 7,
           "frame60": 8, "lod": 9, "frame": 10, "limit": 11, "frame_dt": 12}
FIRST_SLOT = 13
TR_END, TR_BYTES, TR_ABS, TR_REL = 0, 1, 2, 3
SENTINEL = 0x7E5A0000  # symbol k assembles as SENTINEL + k * 0x100, found and replaced after assembly


class CatalogueError(ValueError):
    """A catalogue entry that does not match the stock executable or the format."""


@dataclass(frozen=True)
class Site:
    va: int
    stock: bytes
    kind: str
    klass: str
    what: str
    operand: int = 0          # operand: offset of the 4-byte constant address in the instruction
    type: str = "f32"         # operand: f32 or f64
    transform: str = "scale"  # operand
    slot: str = ""            # operand: a named slot of the [[slot]] table instead of its own
    code: str = ""            # patch or gate: replacement assembly (gate: default the stock bytes)
    alt: str = ""             # gate: what runs on the other ticks (default nothing)
    when: str = "tick60"      # gate: tick60 (a tick's step), once (a step inside a character's update) or frame60
                              # (a stock frame's step, 1/60 s of real time: the draw, the frame end, the catch-up's
                              # frame substitutes)


@dataclass
class Packed:
    sites: list[tuple] = field(default_factory=list)
    slots: list[tuple[int, int, int]] = field(default_factory=list)  # (stock constant VA, f64?, transform)
    pool: bytearray = field(default_factory=bytearray)


@dataclass(frozen=True)
class Slot:
    """A stock constant transformed for the rate, named for code: {slot:NAME}."""
    name: str
    source: int
    type: str = "f32"
    transform: str = "scale"
    what: str = ""


def _tables(path: Path) -> list[dict]:
    files = sorted(path.glob("*.toml")) if path.is_dir() else [path]
    return [tomllib.loads(f.read_text(encoding="utf-8")) for f in files]


def load_slots(path: Path = CATALOGUE) -> list[Slot]:
    slots = [Slot(**entry) for raw in _tables(path) for entry in raw.get("slot", [])]
    if len({s.name for s in slots}) != len(slots):
        raise CatalogueError("two slots share a name")
    for s in slots:
        if s.type not in ("f32", "f64") or s.transform not in TRANSFORMS:
            raise CatalogueError(f"slot {s.name}: type or transform unknown")
    return slots


def load(path: Path = CATALOGUE) -> list[Site]:
    sites = []
    for entry in (entry for raw in _tables(path) for entry in raw.get("site", [])):
        entry = dict(entry)
        stock = bytes.fromhex(entry.pop("stock").replace(" ", ""))
        klass = entry.pop("class")
        site = Site(stock=stock, klass=klass, **entry)
        if site.kind not in KINDS or site.klass not in CLASSES:
            raise CatalogueError(f"{site.va:#x}: kind {site.kind!r} or class {site.klass!r} unknown")
        if site.kind == "operand" and (site.type not in ("f32", "f64") or site.transform not in TRANSFORMS):
            raise CatalogueError(f"{site.va:#x}: operand type or transform unknown")
        if len(stock) > 1024:
            raise CatalogueError(f"{site.va:#x}: a span is at most 1024 bytes")
        if site.kind != "operand" and len(stock) < 5:
            raise CatalogueError(f"{site.va:#x}: a patch or gate needs at least 5 stock bytes for its jump")
        if site.when not in ("tick60", "once", "frame60"):
            raise CatalogueError(f"{site.va:#x}: a gate runs on tick60, once or frame60")
        if site.kind == "patch" and not site.code:
            raise CatalogueError(f"{site.va:#x}: a patch needs code")
        sites.append(site)
    return sorted(sites, key=lambda s: s.va)


def check(exe: bytes, sites: list[Site]) -> None:
    layout = pe.layout(exe)
    end = 0
    for s in sites:
        at = pe.file_offset(layout, s.va)
        if exe[at:at + len(s.stock)] != s.stock:
            raise CatalogueError(f"stock executable lacks the bytes at {s.va:#x}")
        if s.va < end:
            raise CatalogueError(f"sites overlap at {s.va:#x}")
        end = s.va + len(s.stock)
        if s.kind == "operand" and s.operand + 4 > len(s.stock):
            raise CatalogueError(f"{s.va:#x}: operand offset past the instruction")


def assemble(text: str) -> bytes:
    """Bytes of Intel-syntax lines (GNU as of the MinGW toolchain that also builds the DLL)."""
    if shutil.which(ASSEMBLER) is None:
        raise FileNotFoundError(f"{ASSEMBLER} is missing: install Fedora's mingw32-binutils package")
    with tempfile.TemporaryDirectory() as tmp:
        source, obj = Path(tmp, "a.s"), Path(tmp, "a.o")
        source.write_text(".intel_syntax noprefix\n" + text.replace(";", "\n") + "\n", encoding="ascii")
        subprocess.run([ASSEMBLER, "-o", str(obj), str(source)], check=True, capture_output=True)
        image = obj.read_bytes()
    # COFF: the first section (.text) holds the code; its raw size and pointer are in the section header
    sections = struct.unpack_from("<H", image, 2)[0]
    optional = struct.unpack_from("<H", image, 16)[0]
    header = 20 + optional
    for i in range(sections):
        name, _, _, size, pointer = struct.unpack_from("<8sIIII", image, header + 40 * i)
        if name.rstrip(b"\0") == b".text":
            return image[pointer:pointer + size]
    raise CatalogueError("assembler wrote no .text")


def records(text: str, slots: dict[str, int]) -> bytes:
    """Code records (true_rate.h) for assembly with symbols."""
    out = bytearray()
    parts = re.split(r"\{(call|jmp):(0x[0-9a-fA-F]+)\}", text)
    symbols = dict(SYMBOLS)
    symbols.update({f"slot:{name}": FIRST_SLOT + i for name, i in slots.items()})

    def literal(code: bytes):
        for i in range(0, len(code), 255):
            chunk = code[i:i + 255]
            out.extend([TR_BYTES, len(chunk)])
            out.extend(chunk)

    for i, part in enumerate(parts):
        if i % 3 == 0:
            if not part.strip():
                continue
            used = {}

            def sentinel(m: re.Match) -> str:
                name = m.group(1)
                if name not in symbols:
                    raise CatalogueError(f"unknown symbol {{{name}}}")
                used[SENTINEL + 0x100 * symbols[name]] = symbols[name]
                return hex(SENTINEL + 0x100 * symbols[name])

            code = assemble(re.sub(r"\{([a-z0-9_:]+)\}", sentinel, part))
            at = 0
            for k in range(len(code) - 3):
                value = struct.unpack_from("<I", code, k)[0]
                if k >= at and value in used:
                    literal(code[at:k])
                    out.extend([TR_ABS, used[value]])
                    at = k + 4
            literal(code[at:])
        elif i % 3 == 1:
            op, target = part, int(parts[i + 1], 16)
            out.append(TR_BYTES), out.append(1), out.append(0xE8 if op == "call" else 0xE9)
            out.append(TR_REL), out.extend(struct.pack("<I", target))
    out.append(TR_END)
    return bytes(out)


def pack(exe: bytes, sites: list[Site], slots: list[Slot] = ()) -> Packed:
    check(exe, sites)
    layout = pe.layout(exe)
    p = Packed()
    slot_of: dict[str, int] = {}
    for named in slots:
        pe.file_offset(layout, named.source)
        p.slots.append((named.source, named.type == "f64", TRANSFORMS[named.transform]))
        slot_of[named.name] = len(p.slots) - 1
    for s in sites:
        slot, code_at, alt_at = 0, 0, 0
        if s.kind == "operand" and s.slot:
            if s.slot not in slot_of:
                raise CatalogueError(f"{s.va:#x}: no slot {s.slot}")
            slot = slot_of[s.slot]
        elif s.kind == "operand":
            source = struct.unpack_from("<I", s.stock, s.operand)[0]
            pe.file_offset(layout, source)  # raises if the constant is not in the image
            p.slots.append((source, s.type == "f64", TRANSFORMS[s.transform]))
            slot = len(p.slots) - 1
            slot_of[f"{s.va:x}"] = slot
        else:
            code = s.code or " ".join(f".byte {b:#x};" for b in s.stock)
            code_at = len(p.pool)
            p.pool += records(code, slot_of)
            alt_at = len(p.pool)
            p.pool += records(s.alt, slot_of)
        p.sites.append((s.va, len(s.stock), KINDS.index(s.kind), s.operand, SYMBOLS[s.when], slot, code_at, alt_at,
                        s.stock))
    return p


def header(exe: bytes | None, sites: list[Site] | None = None) -> str:
    """C declarations of the packed catalogue for the runtime (exe None with no sites: an empty catalogue)."""
    p = pack(exe, load() if sites is None else sites, load_slots() if sites is None else ()) if exe else Packed()
    hexs = lambda data: ",".join(f"0x{b:02x}" for b in data) or "0"
    stocks, rows = bytearray(), []
    for va, n, kind, operand, when, slot, code, alt, stock in p.sites:
        rows.append(f"    {{{va:#x}, {n}, {kind}, {operand}, {when}, {slot}, {code}, {alt}, {len(stocks)}}},")
        stocks += stock
    slots = [f"    {{{va:#x}, {int(f64)}, {transform}}}," for va, f64, transform in p.slots]
    return "\n".join([
        "/* Generated by wozmod.runtime.true_rate from data/true_rate.toml; do not edit. */",
        f"#define TR_SITES {len(p.sites)}",
        f"#define TR_SLOTS {len(p.slots)}",
        "static const struct tr_site tr_sites[TR_SITES + 1] = {", *rows, "    {0}};",
        "static const struct tr_slot tr_slots[TR_SLOTS + 1] = {", *slots, "    {0}};",
        f"static const tr_u8 tr_pool[{len(p.pool) or 1}] = {{{hexs(p.pool)}}};",
        f"static const tr_u8 tr_stock[{len(stocks) or 1}] = {{{hexs(stocks)}}};",
        "",
    ])

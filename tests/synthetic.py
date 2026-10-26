"""Game-format bytes built from the format description, for tests: string tables, scripts, MESC blocks, archives, PE images."""

from __future__ import annotations

import struct
from pathlib import Path

SECTOR = 2048


def strings(*lines: bytes, order: str = "<") -> bytes:
    """A counted table of offsets, then each line NUL-terminated."""
    offsets, body = [], b""
    for line in lines:
        offsets.append(4 + 4 * len(lines) + len(body))
        body += line + b"\0"
    return struct.pack(f"{order}{len(lines) + 1}I", len(lines), *offsets) + body


def spans(parts, head: bytes = b"", order: str = "<") -> bytes:
    """`head`, an (offset, size) pair per part, then the parts."""
    at, table = len(head) + 8 * len(parts), b""
    for part in parts:
        table += struct.pack(f"{order}II", at, len(part))
        at += len(part)
    return head + table + b"".join(parts)


def script(notices: bytes, dialog: bytes, order: str = "<", events: tuple = ()) -> bytes:
    """A stage script: four section offsets, a filler section, the event section (u32 count and two u32, an offset
    per event, then each event: four bytes, no conditions, a command count and its commands of eight u16), the
    notice table padded to 4, the dialog table."""
    head, body = 12 + 4 * len(events), b""
    offsets = []
    for event in events:
        offsets.append(head + len(body))
        body += bytes([0, 0, 0, 0, 0, len(event)]) + b"".join(struct.pack(f"{order}8H", *c) for c in event)
    section = struct.pack(f"{order}{3 + len(events)}I", len(events), 0, 0, *offsets) + body
    sections = [b"HEAD", section + bytes(-len(section) % 4), notices + bytes(-len(notices) % 4), dialog]
    offsets = [20 + sum(map(len, sections[:i])) for i in range(4)]
    return struct.pack(f"{order}5I", 4, *offsets) + b"".join(sections)


def mesc(*lines: bytes) -> bytes:
    """A MESC block: count, total size, an offset per line, then each line NUL-terminated."""
    offsets, body = [], b""
    for line in lines:
        offsets.append(12 + 4 * len(lines) + len(body))
        body += line + b"\0"
    return b"MESC" + struct.pack(f"<II{len(lines)}I", len(lines), 12 + 4 * len(lines) + len(body), *offsets) + body


def container(*entries: bytes) -> bytes:
    """A LINKDATA1 container: magic, count, 8 bytes, (offset, size) pairs in 16-byte units, each entry 16-aligned."""
    head = 16 + 8 * len(entries)
    at, pairs, body = -(-head // 16), [], b""
    for entry in entries:
        pairs += [at + len(body) // 16, len(entry)]
        body += entry + bytes(-len(entry) % 16)
    return (struct.pack(f"<4I{2 * len(entries)}I", 0x33B59, len(entries), 0, 0, *pairs) + bytes(at * 16 - head)
            + body)


def event_file(*scenes: list[tuple[int, int, bytes]], order: str = "<", gap: int = 0) -> tuple[bytes, list[int]]:
    """An event file of sub-scenes, each a chain of (type, kind, text) records with one u32 field, the offset of a
    0xFFFFFFFF mask and the text, from the sub-scene start; gap zero bytes sit before each sub-scene. Returns the
    file and the file offset of each text, in order."""
    head = 4 + 4 * len(scenes)
    body, bases, texts = b"", [], []
    for records in scenes:
        body += bytes(gap)
        base, chain, tail = head + len(body), b"", b""
        start = 16 * len(records) + 8  # the records, then the 0xFF record that ends the chain
        for i, (kind, sub, text) in enumerate(records):
            chain += struct.pack(f"{order}I4B2BxxI", 16 * (i + 1), kind, sub, 1, 7, 5, 4, start + len(tail))
            texts.append(base + start + len(tail) + 4)
            tail += b"\xff" * 4 + text + b"\0"
        bases.append(base)
        body += chain + struct.pack(f"{order}I4B", 0, 0xFF, 0, 0, 0) + tail
    return struct.pack(f"{order}{len(scenes) + 1}I", len(scenes), *bases) + body, texts


def timed_event(lines: list[tuple[float, float, int, bytes]], length: float, order: str = "<",
               captions: tuple[tuple[float, bytes], ...] = ()) -> bytes:
    """One sub-scene: for each (speak tick, clear tick, line index, text) a gate, a speak, a gate and a clear, and for
    each (tick, text) a gate and a place caption, in tick order; then its length and the end record. The line records
    follow the chain, each at an offset 12 past a 16 boundary as the stock files have them."""
    f32, u16, u32 = (lambda v: struct.pack(f"{order}f", v)), (lambda v: struct.pack(f"{order}H", v)), \
        (lambda v: struct.pack(f"{order}I", v))
    events = sorted([(t, 1, i, text) for t, _, i, text in lines] + [(t, 2, 0, b"") for _, t, _, _ in lines]
                    + [(t, 0, 0, text) for t, text in captions])
    base, chain, tail, _texts = 16, [], bytearray(), []
    for tick, what, index, text in events:
        chain.append(("gate", tick))
        chain.append(("speak" if what == 1 else "clear" if what == 2 else "caption", (index, text)))
    chain += [("length", length), ("end", None)]
    sizes = {"gate": 16, "speak": 20, "clear": 8, "caption": 16, "length": 16, "end": 8}
    at = [0]
    for kind, _ in chain:
        at.append(at[-1] + sizes[kind])
    strings = at[-1] + (12 - (base + at[-1]) % 16) % 16  # the first line record, 12 past a 16 boundary
    out = bytearray()
    for n, (kind, value) in enumerate(chain):
        following = at[n + 1] if kind != "end" else 0
        if kind in ("gate", "length"):
            body = bytes([0, 0 if kind == "gate" else 1, 1, 6, 5, 4, 0, 0]) + f32(value)
        elif kind == "clear":
            body = bytes([5, 1, 0, 0])
        elif kind == "end":
            body = bytes([0xFF, 0, 0, 0])
        else:
            index, text = value
            offset = strings + len(tail)
            record = (b"\xff" * 4 + (u16(index) + u16(0) if kind == "speak" else b"") + text + b"\0")
            tail += record + bytes((12 - (base + strings + len(tail) + len(record)) % 16) % 16)
            if kind == "speak":
                body = bytes([5, 5, 2, 4, 8, 5, 4, 0]) + u16(0) + bytes(2) + u32(offset)
            else:
                body = bytes([5, 0, 1, 7, 5, 4, 0, 0]) + u32(offset)
        out += u32(following) + body
        assert len(u32(0) + body) == sizes[kind], kind
    out += bytes(strings - len(out)) + tail
    return u32(1) + u32(base) + bytes(base - 8) + bytes(out)


def write_stock_archive(folder: Path, stem: str, entries: dict, count: int | None = None) -> None:
    """Write `stem`.BIN and .IDX: an entry is bytes, or a tuple of parts that share one record."""
    records, trailer, body = [], b"", b""
    for ordinal in range(max(entries) + 1 if count is None else count):
        parts = entries.get(ordinal, b"")
        parts = parts if isinstance(parts, tuple) else (parts,)
        if len(parts) == 1:
            records.append((len(parts[0]), 0, 0))
        else:
            records.append((1, sum(len(p) + -len(p) % SECTOR for p in parts), len(trailer)))
            trailer += struct.pack(f"<{len(parts) + 1}I", *map(len, parts), 0)
        body += b"".join(p + bytes(-len(p) % SECTOR) for p in parts)
    (folder / f"{stem}.BIN").write_bytes(body)
    (folder / f"{stem}.IDX").write_bytes(b"".join(struct.pack("<3I", *r) for r in records) + trailer)


def write_ens(folder: Path, entries: dict[int, bytes], count: int | None = None) -> Path:
    """Write a donor linkdata_ens.idx and .lnk (big-endian SM4L index) and return their shared stem."""
    records, body, sector = b"", b"", 0
    for ordinal in range(max(entries) + 1 if count is None else count):
        value = entries.get(ordinal, b"")
        allocation = -(-len(value) // SECTOR)
        records += struct.pack(">4I", sector, allocation, len(value), 0)
        body += value + bytes(-len(value) % SECTOR)
        sector += allocation
    stem = folder / "linkdata_ens"
    stem.with_suffix(".idx").write_bytes(struct.pack(">4sIII", b"SM4L", len(records) // 16, sector, 0) + records)
    stem.with_suffix(".lnk").write_bytes(body)
    return stem


def write_ps2_archive(path: Path, entries: dict[int, bytes], count: int | None = None) -> Path:
    """Write a PSP/PS2-layout archive: header, a (sector, size) pair per member, each member at its own sector."""
    count = max(entries) + 1 if count is None else count
    table = 16 + 8 * count
    sector, index, body = -(-table // SECTOR), b"", b""
    for ordinal in range(count):
        data = entries.get(ordinal, b"")
        index += struct.pack("<II", sector, len(data))
        body += data + bytes(-len(data) % SECTOR)
        sector += -(-len(data) // SECTOR)
    head = struct.pack("<4I", 0x33B59, count, SECTOR, 0) + index
    path.write_bytes(head + bytes(-len(head) % SECTOR) + body)
    return path


def pe_image(size: int, *sections: tuple[bytes, int, int, int, int], image_base: int = 0x400000,
             pe_at: int = 0x80, optional_size: int = 0xE0) -> bytearray:
    """An i386 PE: its header at pe_at, an optional header of optional_size, then (name, vsize, rva, raw_size, raw)."""
    image = bytearray(size)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, pe_at)
    struct.pack_into("<4sHH12xH", image, pe_at, b"PE\0\0", 0x14C, len(sections), optional_size)
    struct.pack_into("<I", image, pe_at + 24 + 28, image_base)
    for i, section in enumerate(sections):
        struct.pack_into("<8sIIII", image, pe_at + 24 + optional_size + 40 * i, *section)
    return image


def stock_game(folder: Path, exe: dict | None = None, linkdata1: dict | None = None, linkdata2: dict | None = None,
               clips: dict | None = None, voice_tail: bytes = b"", clip_order: tuple = ()):
    """Write a small game folder laid out as the stock one, and return the pieces record (install.stock.Recorded) of
    the unchanged folder. Each argument replaces stock pieces in what is written: exe {offset: bytes},
    linkdata1/linkdata2 {entry: bytes or parts}, clips {clip: bytes}; voice_tail is appended to Voice.bin, and
    clip_order lays the clips out in Voice.bin in another order.

    The record: exe spans 0x200 (text) and 0x300 (text, art); LINKDATA1 2 read stored by art, 3 (LZP2) read unpacked
    by text, 1 unread; LINKDATA2 0 read unpacked by voices, 2 only served; Voice.bin clips 0 and 3 read by voices, 1,
    2 and 4 only served. LINKDATA1 4058 is the voice index."""
    import hashlib

    from wozmod.formats.linkdata import encode_lzp2
    from wozmod.install.stock import EXE, VOICE, VOICE_INDEX, Piece, Recorded

    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
    stamp = (0x4AF69DF7, 0x101A000)  # 2009-11-08 10:31:19 UTC, version 1.00's
    image = bytearray(bytes(range(256)) * 4)
    image[:0x200] = bytes(0x200)
    image[:2], image[0x3C:0x40] = b"MZ", (0x80).to_bytes(4, "little")
    image[0x80:0x86] = b"PE\0\0" + (0x14C).to_bytes(2, "little")
    image[0x88:0x8C] = stamp[0].to_bytes(4, "little")
    image[0x80 + 24 + 56:0x80 + 24 + 60] = stamp[1].to_bytes(4, "little")
    stock_clips = {0: b"clip zero " * 30, 1: b"clip one " * 20, 2: b"clip two " * 10, 3: b"clip three " * 5,
                   4: b"clip four " * 7}
    ld1 = {1: b"unread entry", 2: b"art entry " * 300, 3: encode_lzp2(b"text entry " * 40)}
    ld2 = {0: b"stage script " * 50, 1: b"another unread entry", 2: b"caption schedule"}
    record = Recorded(stamp, (
        Piece(EXE, 0x200, sha(bytes(image[0x200:0x220])), frozenset({"text"}), length=0x20),
        Piece(EXE, 0x300, sha(bytes(image[0x300:0x310])), frozenset({"text", "art"}), length=0x10),
        Piece("LINKDATA1", 2, sha(ld1[2]), frozenset({"art"})),
        Piece("LINKDATA1", 3, sha(ld1[3]), frozenset({"text"}), decoded_sha256=sha(b"text entry " * 40)),
        Piece("LINKDATA2", 0, sha(ld2[0]), frozenset({"voices"}), decoded_sha256=sha(ld2[0])),
        Piece("LINKDATA2", 2, sha(ld2[2]), frozenset(), served=True),
        Piece(VOICE, 0, sha(stock_clips[0]), frozenset({"voices"})),
        Piece(VOICE, 1, sha(stock_clips[1]), frozenset(), served=True),
        Piece(VOICE, 2, sha(stock_clips[2]), frozenset(), served=True),
        Piece(VOICE, 3, sha(stock_clips[3]), frozenset({"voices"})),
        Piece(VOICE, 4, sha(stock_clips[4]), frozenset(), served=True),
    ))
    for offset, value in (exe or {}).items():
        image[offset:offset + len(value)] = value
    folder.mkdir(parents=True, exist_ok=True)
    (folder / EXE).write_bytes(image)
    every, body, where = {**stock_clips, **(clips or {})}, b"", {}
    for clip in clip_order or sorted(every):
        where[clip] = (len(body), len(every[clip]))
        body += every[clip] + bytes(-len(every[clip]) % 16)
    table = b"".join(struct.pack(">II", *where[clip]) for clip in sorted(every))
    write_stock_archive(folder, "LINKDATA1", {**ld1, **(linkdata1 or {}),
                                              VOICE_INDEX: struct.pack(">II", len(every), 0) + table}, count=VOICE_INDEX + 1)
    write_stock_archive(folder, "LINKDATA2", {**ld2, **(linkdata2 or {})})
    (folder / VOICE).parent.mkdir(parents=True, exist_ok=True)
    (folder / VOICE).write_bytes(body + voice_tail)
    return record


def fake_step_keys(monkeypatch, keys: dict | None = None) -> dict:
    """Give the installer fixed step keys (and no movies), so a synthetic install neither parses the source for them
    nor reads data/movies.json; change the returned dict's keys to make a step look changed."""
    from wozmod.install import step_keys

    keys = keys if keys is not None else {step: {"key": f"{step} key", "parts": {"mod": step}} for step in step_keys.STEPS}
    monkeypatch.setattr(step_keys, "keys", lambda root=None, toolchain=None: {s: dict(k) for s, k in keys.items()})
    monkeypatch.setattr(step_keys, "movie_keys", lambda: {})
    return keys

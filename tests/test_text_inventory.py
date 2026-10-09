import struct
from pathlib import Path

from wozmod.text.inventory import inventory


def _cp932(ordinal):
    return chr(0x3042 + 2 * ordinal).encode("cp932")


def _strings(*values):
    head = 4 * (len(values) + 1)
    offsets = [head + sum(len(v) + 1 for v in values[:i]) for i in range(len(values))]
    return struct.pack(f"<{len(values) + 1}I", len(values), *offsets) + b"".join(v + b"\0" for v in values)


def _script(notice, spoken):
    notices = _strings(b"32" + notice, b"00ASCII", b"00\x81\x40 \n")
    parts = [b"AAAA", b"BBBB", notices + bytes(-len(notices) % 4), _strings(b"00" + spoken + b"@0042")]
    offsets = [20 + sum(map(len, parts[:i])) for i in range(4)]
    return struct.pack("<5I", 4, *offsets) + b"".join(parts)


def _mesc(value):
    return b"MESC" + struct.pack("<3I", 1, 17 + len(value), 16) + value + b"\0"


def _spans(parts, head=b""):
    offset, pairs = len(head) + 8 * len(parts), []
    for part in parts:
        pairs.extend((offset, len(part)))
        offset += len(part)
    return head + struct.pack(f"<{len(pairs)}I", *pairs) + b"".join(parts)


def _archive(root: Path, name, entries):
    records, trailer, payload = [], b"", b""
    for ordinal in range(max(entries) + 1):
        parts = entries.get(ordinal, ())
        if not parts:
            records.append((0, 0, 0))
            continue
        allocation = sum((len(p) + 2047) // 2048 * 2048 for p in parts)
        if len(parts) == 1:
            records.append((len(parts[0]), 0, 0))
        else:
            records.append((1, allocation, len(trailer)))
            trailer += struct.pack(f"<{len(parts) + 1}I", *(len(p) for p in parts), 0)
        for part in parts:
            payload += part + bytes(-len(part) % 2048)
    (root / f"{name}.BIN").write_bytes(payload)
    (root / f"{name}.IDX").write_bytes(b"".join(struct.pack("<3I", *r) for r in records) + trailer)


def _exe(messages):
    data = bytearray(0x4200)
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x14C, 1)
    struct.pack_into("<H", data, 0x94, 0xE0)
    struct.pack_into("<I", data, 0x98 + 28, 0x6DE000)
    struct.pack_into("<8sIIII", data, 0x178, b".data", 0x4000, 0x100, 0x4000, 0x200)
    at = 0x1800
    for slot in range(911):
        value = messages.get(slot, b"ASCII")
        struct.pack_into("<I", data, 0x5F0 + slot * 4, 0x6DE000 + at - 0x100)
        data[at:at + len(value) + 1] = value + b"\0"
        at += len(value) + 1
    return bytes(data)


def test_inventory_reads_the_supplied_root_without_dropping_boundary_rows(tmp_path):
    values = [_cp932(i) for i in range(20)]
    cue = lambda text: struct.pack("<3I", 1, 5, 9) + text + b"\0" + _cp932(30) + b"\0"
    event = lambda text: b"\xff" * 4 + text + _cp932(31) + b"\0"
    _archive(tmp_path, "LINKDATA2", {
        1: (_script(values[0], values[1]),),
        2: (_script(values[2], values[3]), b"tail"),
        145: (cue(values[4]),),
        176: (cue(values[5]),),
        177: (event(values[6]),),
        413: (event(values[7]),),
    })
    quotes = _spans([
        _strings(b"33" + values[13]), _strings(b"32" + values[14]),
        _spans([_strings(b"00" + values[15] + b"@0099"), _strings(b"00" + values[16])]),
    ], struct.pack("<I", 3))
    _archive(tmp_path, "LINKDATA1", {
        4071: (_mesc(values[8]),), 4073: (_mesc(values[9]),),
        4103: (_mesc(values[10]),), 4105: (_mesc(values[11]),),
        4190: (_mesc(values[12]),), 4084: (quotes,),
    })
    (tmp_path / "OROCHI_Z.exe").write_bytes(_exe({0: values[17], 455: values[18], 910: values[19]}))

    assert inventory(tmp_path) == {
        "notices": {values[0]: ("window", 2)},
        "spoken": {values[1]: ("window", 1)},
        "captions": {values[4]: 0x56A23F, values[5]: 0x56A23F},
        "events": {values[6] + _cp932(31): None, values[7] + _cp932(31): None},
        "mesc 4071": {values[8]: 0x561111}, "mesc 4073": {values[9]: 0x561111},
        "mesc 4103": {values[10]: 0x59C7BD}, "mesc 4105": {values[11]: 0x59C7BD},
        "briefings": {values[12]: 0x548F52},
        "quote formats": {values[13]: ("window", 2), values[14]: ("window", 2)},
        "officer quotes": {values[15]: ("window", 1), values[16]: ("window", 1)},
        "exe messages": {values[17]: None, values[18]: None, values[19]: None},
    }

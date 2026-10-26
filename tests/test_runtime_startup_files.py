"""The DLL's checks of the files the installer wrote, run before it patches anything (runtime/startup_files.h): the
install record, table.bin, Slots.bin and the pad prompt packages, read as the Python side writes them."""

from __future__ import annotations

import ctypes
import json
import struct

import pytest

from wozmod.install import _write_manifest
from wozmod.runtime.english_runtime import table_blob
from wozmod.voices.maps import VoiceSlot, slot_table

GLUE = r'''
#include "startup_files.h"
/* Every name the record lists, NUL-separated into out; how many, or -1 where the record cannot be read. */
int record_names(const char *json, char *out, unsigned size)
{
    const char *at = record_files(json);
    int count = 0, next;
    if (!at)
        return -1;
    while ((next = record_next(&at, out, size)) > 0) {
        while (*out)
            out++, size--;
        out++, size--, count++;
    }
    return next < 0 ? -1 : count;
}
int table(const unsigned *t, unsigned size) { return table_ok(t, size); }
int slots(const unsigned char *d, unsigned size) { return slots_ok(d, size); }
int package(const unsigned char *d, unsigned size, const unsigned char *slots, unsigned n)
{
    return package_items_ok(d, size, slots, n);
}
'''


@pytest.fixture(scope="module")
def files(c_library):
    lib = c_library("startup_files", GLUE)
    lib.record_names.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    lib.table.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    lib.slots.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    lib.package.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint]
    return lib


def _names(files, json: bytes) -> list[str] | None:
    out = ctypes.create_string_buffer(1 << 16)
    count = files.record_names(json, out, len(out))
    return None if count < 0 else [n.decode() for n in out.raw.split(b"\0")[:count]]


def test_the_dll_reads_every_file_the_installers_record_lists(files, tmp_path):
    """Bugs: the first or the last file of the record is dropped (the ',' and '}' around it), a hash is read as a
    file, so a missing file goes unnoticed or a present one turns wozmod off; a record cut short or with an escaped
    name is read as a complete one. The record is written by the installer's own writer."""
    listed = {"d3d9.dll": "a" * 64, "wozmod/captions/4397.bin": "b" * 64, "wozmod/entries/12.bin": "c" * 64,
              "wozmod/voice/Voice.bin": "d" * 64}
    (tmp_path / "wozmod").mkdir()
    _write_manifest(tmp_path, listed)
    record = (tmp_path / "wozmod/install.json").read_bytes()
    assert _names(files, record) == sorted(listed)
    _write_manifest(tmp_path, {})
    assert _names(files, (tmp_path / "wozmod/install.json").read_bytes()) == []
    unreadable = {"cut short": record[:record.index(b"wozmod/entries")],
                  "escaped name": record.replace(b"wozmod/entries/12.bin", b"wozmod/entries/1\\u0032.bin"),
                  "hash not a string": record.replace(b'"' + b"c" * 64 + b'"', b"12"),
                  "no files": record.replace(b'"files"', b'"filed"')}
    assert {case: _names(files, json) for case, json in unreadable.items()} == dict.fromkeys(unreadable)


def test_the_dll_reads_the_records_own_file_list_wherever_its_sections_put_theirs(files):
    """Bugs: the DLL takes the first "files" key anywhere in the record, so a format 2 record whose steps (each with
    its own "files" list) come first is refused and wozmod is off for every player; or it takes the last one, so the
    steps after the list do the same; or it reads a section's list when the record has none of its own, and checks
    other files than the installed ones. Records are hand-built in each key order, not by the installer's writer."""
    listed = {"d3d9.dll": "a" * 64, "wozmod/entries/12.bin": "b" * 64}
    steps = {"text": {"key": "c" * 64, "parts": {"mod": "d" * 64}, "files": ["wozmod/entries/12.bin"]},
             "art": {"key": "e" * 64, "parts": {}, "files": []}}
    records = {
        "format 1": {"format": 1, "version": "1.00", "files": listed},
        "format 2, steps first": {"format": 2, "version": "1.01", "platform": "win32", "steps": steps, "files": listed},
        "format 2, steps after": {"format": 2, "version": "1.01", "platform": "win32", "files": listed, "steps": steps},
        "only a section's list": {"format": 2, "steps": {"text": {"files": {"wozmod/other.bin": "f" * 64}}}},
    }
    got = {case: _names(files, json.dumps(record, indent=1).encode()) for case, record in records.items()}
    assert got == {"format 1": sorted(listed), "format 2, steps first": sorted(listed),
                   "format 2, steps after": sorted(listed), "only a section's list": None}


def test_the_dll_takes_the_table_the_build_writes_and_refuses_one_cut_short(files):
    """Bug: the DLL's check refuses the table the installer writes (wozmod off for every player), or takes one cut
    short in its name table or with more slots than it holds, whose lookups then read past its end."""
    blob = table_blob({b"key %d" % i: b"value %d" % i for i in range(40)}, [b"name", None, b"other"], [1])
    mask, names = struct.unpack_from("<II", blob)
    assert files.table(blob, len(blob)) == 1
    cuts = (names, names + 4 * (1 + 3) - 4)  # at the name table, and in it (its count says 3)
    assert [files.table(blob[:n], n) for n in cuts] == [0, 0]
    slots = bytearray(blob)
    slots[:4] = struct.pack("<I", 2 * mask + 1)  # twice the slots, past the end
    assert files.table(bytes(slots), len(slots)) == 0


def test_the_dll_takes_the_slots_the_build_writes_and_refuses_them_cut_short(files):
    """Bug: the DLL's check refuses the Slots.bin the voice build writes, empty or not (wozmod off for every player),
    or takes one cut short, whose last row then reads past its end."""
    rows = slot_table({(3, 7): VoiceSlot(0x6000, (0x100, 0x101), "a", 0), (4, 2): VoiceSlot(0x6001, (0x200,), "b", 0)})
    for blob in (slot_table({}), rows):
        assert files.slots(blob, len(blob)) == 1
    assert [files.slots(rows[:n], n) for n in (0, 3, len(rows) - 8, len(rows) - 1)] == [0, 0, 0, 0]


def _package(items: list[bytes]) -> bytes:
    """A texture package as the game and wozmod.formats.textures lay it out: magic, count, alignment 16, 0; per item
    its offset in 16-byte units and its size; each item at its offset, padded to 16."""
    head = struct.pack("<4I", 0x33B59, len(items), 16, 0)
    position = (len(head) + 8 * len(items) + 15) // 16 * 16
    records, body = b"", b""
    for item in items:
        records += struct.pack("<II", position // 16, len(item))
        body += item + bytes(-len(item) % 16)
        position += len(item) + -len(item) % 16
    table = head + records
    return table + bytes(-len(table) % 16) + body


def _g1t(size: int) -> bytes:
    return b"GT1G0600" + struct.pack("<I", size) + bytes(size - 12)


def test_the_dll_finds_each_prompt_slots_item_and_refuses_a_package_without_it(files):
    """Bugs: an item's offset word is read as bytes instead of 16-byte units (the game multiplies it by the
    alignment), so the check refuses every install's prompts; a slot past the item count, or an item cut short at the
    end of the file, is accepted, and the game reads past the package."""
    package = _package([_g1t(40), _g1t(100), _g1t(64)])
    slots = bytes([0, 2])
    assert files.package(package, len(package), slots, 2) == 1
    fewer = bytearray(package)
    fewer[4:8] = struct.pack("<I", 2)  # the count says 2: the third record is past the table, whatever it holds
    assert files.package(bytes(fewer), len(fewer), bytes([2]), 1) == 0
    assert files.package(package[:-20], len(package) - 20, slots, 2) == 0
    first = bytearray(package)
    first[16:20] = struct.pack("<I", 20)  # the first item's offset moved past the file
    assert files.package(bytes(first), len(first), slots, 2) == 0

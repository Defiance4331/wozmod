"""tools/disasm.py on a synthetic exe."""

from __future__ import annotations

import struct

import disasm
from synthetic import pe_image


def test_refs_finds_calls_jumps_immediates_and_pointers_to_an_address(tmp_path, capsys):
    """Bugs: a call or jcc is matched against the wrong end of its instruction (rel32 is from the next one), so it is
    missed or a neighbour is reported; a pointer in a data section is skipped; an immediate is decoded from a wrong
    start."""
    image = pe_image(0x800, (b".text", 0x200, 0x1000, 0x200, 0x400), (b".data", 0x200, 0x2000, 0x200, 0x600))
    struct.pack_into("<I", image, 0x80 + 24 + 0xE0 + 36, 0x60000020)  # .text holds code
    code = (b"\xe8\xfb\x00\x00\x00"          # 401000 call 0x401100
            b"\x0f\x84\xf5\x00\x00\x00"      # 401005 je 0x401100
            b"\xb8\x00\x11\x40\x00"          # 40100b mov eax, 0x401100
            b"\xe8\x00\x00\x00\x00")         # 401010 call 0x401015: another target
    image[0x400:0x400 + len(code)] = code
    struct.pack_into("<I", image, 0x610, 0x401100)  # 402010: a pointer to it
    (tmp_path / "OROCHI_Z.exe").write_bytes(image)
    assert disasm.main(["--exe", str(tmp_path / "OROCHI_Z.exe"), "refs", "401100"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "401000  call 0x401100",
        "401005  je 0x401100",
        "40100b  mov eax, 0x401100  (guessed start)",
        "402010  value in .data",
    ]

"""The movie caption loaders' constants (contexts.MOVIE_CAPTION_LOADERS, MOZ_CAPTION_KEY) against the programs."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from wozmod.formats import pe
from wozmod.text import contexts

X360_BASE = 0x82000000  # VA = 0x82000000 + default.pe offset


def _program(donor: str) -> bytes:
    program = Path(os.environ.get("WOZMOD_DONORS", ""), donor, "default.pe")
    if not os.environ.get("WOZMOD_DONORS") or not program.is_file():
        pytest.skip("set WOZMOD_DONORS to a folder holding the decrypted X360 programs")
    return program.read_bytes()


def _immediate(program: bytes, va: int, opcode: int) -> int:
    """The signed 16-bit immediate of the instruction at va, which must have the given primary opcode."""
    word = struct.unpack_from(">I", program, va - X360_BASE)[0]
    assert word >> 26 == opcode, f"{va:#x} holds {word:#010x}, not the instruction the loader had"
    value = word & 0xFFFF
    return value - 0x10000 * (value >= 0x8000 and opcode != 10)


@pytest.mark.parametrize("donor", ["wo2-x360", "wo1-x360"])
def test_a_donor_movie_loads_the_caption_schedule_its_loader_computes(donor) -> None:
    # Bug: movie_schedule's movie range, caption id or first ENS id differs from the donor loader's, so a donor movie
    # is paired with its neighbour's schedule (ENS 245 + nn, FNS 367 + nn) or a movie the loader skips gets one.
    # Expected: the immediates of WO2 X360 0x822B9EB0 and WO1 X360 0x822754A0 (addi 14, li/addi 14, cmpwi 11,
    # cmplwi 10).
    program = _program(donor)
    movies, caption_id, first_id, _ = contexts.MOVIE_CAPTION_LOADERS[donor]
    if donor == "wo2-x360":  # n == 0 and n >= 0x1A load nothing
        found = (range(_immediate(program, 0x822B9FA8, 11) + 1, _immediate(program, 0x822B9FB0, 11)),
                 _immediate(program, 0x822B9EEC, 14), _immediate(program, 0x822B9EF0, 14))
    else:  # n - 2 <= 3 unsigned
        low = -_immediate(program, 0x822754B4, 14)
        found = (range(low, low + _immediate(program, 0x822754B8, 10) + 1),
                 _immediate(program, 0x822754F8, 14), _immediate(program, 0x822754FC, 14))
    assert (movies, caption_id, first_id) == found


def test_a_stock_movie_slot_reads_the_entry_its_loader_keys() -> None:
    # Bug: movie_slot_entry's base off by one, so every slot reads its neighbour's schedule. Expected: the stock
    # loader 0x56A2C0 adds 0x112C to the slot index (add eax, imm32 at 0x56A319).
    game = os.environ.get("WOZMOD_GAME")
    if not game or not Path(game, "OROCHI_Z.exe").is_file():
        pytest.skip("set WOZMOD_GAME to the stock game folder")
    exe = Path(game, "OROCHI_Z.exe").read_bytes()
    at = pe.file_offset(pe.layout(exe), 0x56A319)
    assert exe[at] == 0x05 and struct.unpack_from("<I", exe, at + 1)[0] == contexts.MOZ_CAPTION_KEY

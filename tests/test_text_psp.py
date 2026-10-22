import hashlib
from dataclasses import replace

import pytest

from wozmod.donors import DISCS, ProgramError, ProgramIdentity
from wozmod.text import DonorResolver, psp_exe


def test_psp_pool_keeps_invalid_slots_instead_of_shifting_later_pointers():
    data = b"\0Alpha\0\x81\x82\0B\0Bravo \xd3\0\0Tail\0"

    assert psp_exe.eboot_strings(data) == ["Alpha", None, "Bravo \u014c", "Tail"]


def test_a_wrong_psp_program_cannot_supply_string_slots(tmp_path):
    """Bug: the PSP pool reads the staged program without checking its pin, or looks for it in another folder."""
    (tmp_path / "wo2-psp").mkdir()
    (tmp_path / "wo2-psp/eboot.prx").write_bytes(b"\0Unrelated fixture\0")

    with pytest.raises(ProgramError, match="wrong size or SHA-256"):
        DonorResolver(tmp_path).resolve(["wo2-psp", "prx", 0])


def test_the_psp_pool_reads_the_staged_program_without_its_tail(tmp_path, monkeypatch):
    """Bug: bytes after the pinned program are read as more string slots."""
    data = b"\0Alpha\0Bravo\0"
    psp = DISCS["wo2-psp"]
    pin = ProgramIdentity("test", len(data), hashlib.sha256(data).hexdigest(), "psp")
    monkeypatch.setitem(DISCS, "wo2-psp", replace(psp, program=replace(psp.program, plain=pin)))
    (tmp_path / "wo2-psp").mkdir()
    (tmp_path / "wo2-psp/eboot.prx").write_bytes(data + b"Tail\0")
    resolver = DonorResolver(tmp_path)

    assert resolver.resolve(["wo2-psp", "prx", 1]) == "Bravo"
    with pytest.raises(IndexError):
        resolver.resolve(["wo2-psp", "prx", 2])


def test_stock_pool_keeps_file_order_duplicates_and_the_400_byte_boundary():
    key = "\u3042\u3044"
    data = b"Leading\0Second\0A\0" + key.encode("cp932") + b"\0\x81\0Second\0"
    data += b"X" * 400 + b"\0" + b"Y" * 401 + b"\0Unterminated"
    keys = {"Leading", "Second", "A", key, "X" * 400, "Y" * 401, "Unterminated"}

    assert psp_exe.exe_strings(data, keys) == ["Second", "A", key, "Second", "X" * 400]

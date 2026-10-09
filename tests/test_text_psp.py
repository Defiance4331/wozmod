import hashlib

import pytest

from wozmod.text import psp_exe


def test_psp_pool_keeps_invalid_slots_instead_of_shifting_later_pointers(tmp_path, monkeypatch):
    data = b"\0Alpha\0\x81\x82\0B\0Bravo \xd3\0\0Tail\0"
    path = tmp_path / "program.prx"
    path.write_bytes(data)
    monkeypatch.setattr(psp_exe, "EBOOT_SHA256", hashlib.sha256(data).hexdigest())

    assert psp_exe.eboot_strings(path) == ["Alpha", None, "Bravo \u014c", "Tail"]


def test_encrypted_psp_input_is_refused_even_when_its_hash_matches(tmp_path, monkeypatch):
    data = b"~PSP\0fixture\0"
    path = tmp_path / "program.prx"
    path.write_bytes(data)
    monkeypatch.setattr(psp_exe, "EBOOT_SHA256", hashlib.sha256(data).hexdigest())

    with pytest.raises(ValueError, match="still encrypted"):
        psp_exe.eboot_strings(path)


def test_wrong_psp_program_cannot_supply_string_slots(tmp_path):
    path = tmp_path / "program.prx"
    path.write_bytes(b"\0Unrelated fixture\0")

    with pytest.raises(ValueError, match="not the decrypted"):
        psp_exe.eboot_strings(path)


def test_stock_pool_keeps_file_order_duplicates_and_the_400_byte_boundary():
    key = "\u3042\u3044"
    data = b"Leading\0Second\0A\0" + key.encode("cp932") + b"\0\x81\0Second\0"
    data += b"X" * 400 + b"\0" + b"Y" * 401 + b"\0Unterminated"
    keys = {"Leading", "Second", "A", key, "X" * 400, "Y" * 401, "Unterminated"}

    assert psp_exe.exe_strings(data, keys) == ["Second", "A", key, "Second", "X" * 400]

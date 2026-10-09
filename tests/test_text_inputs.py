import hashlib
import os
from pathlib import Path

import pytest

from wozmod.text import DonorResolver
from wozmod.text.inputs import extract_text_program


def test_wo1_disc_program_lands_where_default_text_resolver_reads(tmp_path):
    image = os.environ.get("WOZMOD_WO1_ISO")
    if not image or not Path(image).is_file():
        pytest.skip("WOZMOD_WO1_ISO must point to the WO1 disc image")
    root = tmp_path / "donors"
    extracted = extract_text_program(Path(image), root)
    program = DonorResolver(root).program("wo1")

    assert (extracted, hashlib.sha256(program).hexdigest()) == (
        root / "wo1/OROCHI.exe",
        "387c45a2631ba0ee72830fb9ddb90dc813fa19c7764eb932d5f65e234fe28b2b",
    )

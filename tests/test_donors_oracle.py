import hashlib
import os
from pathlib import Path

import pytest

from wozmod.donors import DONORS, extract_donor


@pytest.mark.skipif(os.environ.get("WOZMOD_DISCS") != "1", reason="Full disc oracle is opt-in")
@pytest.mark.parametrize("donor_name,variable", [
    ("wo2-x360", "WOZMOD_X360_ISO"),
    ("wo2-psp", "WOZMOD_PSP_ISO"),
])
def test_disc_extraction_does_not_drop_or_change_a_hand_extracted_donor_file(
    tmp_path, donor_name, variable,
):
    image = Path(os.environ[variable]) if os.environ.get(variable) else None
    old = Path(os.environ["WOZMOD_OLD_REPO"]) if os.environ.get("WOZMOD_OLD_REPO") else None
    if image is None or not image.is_file() or old is None:
        pytest.skip("Donor disc and old extraction oracle are required")
    oracle = old / "work/donors" / donor_name
    if not oracle.is_dir():
        pytest.skip("Old donor extraction is absent")
    excluded = {"default.pe", "eboot.prx", "SHA256SUMS.txt"}
    expected = {p.relative_to(oracle).as_posix(): p for p in oracle.rglob("*")
                if p.is_file() and p.name not in excluded}
    assert {member.path for member in DONORS[donor_name].files} == expected.keys()
    destination = tmp_path / donor_name
    extract_donor(image, DONORS[donor_name], destination)
    actual = {p.relative_to(destination).as_posix(): p for p in destination.rglob("*") if p.is_file()}
    assert actual.keys() == expected.keys()
    failures = []
    for name, path in expected.items():
        with actual[name].open("rb") as got, path.open("rb") as want:
            if hashlib.file_digest(got, "sha256").digest() != hashlib.file_digest(want, "sha256").digest():
                failures.append(name)
    assert failures == []

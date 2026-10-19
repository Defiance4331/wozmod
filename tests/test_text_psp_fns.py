import hashlib
import json
import os
import re
import struct
from pathlib import Path

import pytest

from wozmod.donors import Donor, DonorFile, PSP, extract_donor
from wozmod.formats.linkdata import decoded_member
from wozmod.formats.scripts import caption_cues, donor_text, mesc_blocks, parse_script, quote_tables
from wozmod.text import DonorResolver


def test_fns_pointer_keeps_its_archive_member_and_notice_slot(tmp_path, monkeypatch):
    records = [b"32Authored first", b"11Authored second"]
    notices = struct.pack("<3I", 2, 12, 12 + len(records[0]) + 1) + b"\0".join(records) + b"\0"
    notices += bytes(-len(notices) % 4)
    script = struct.pack("<5I", 4, 20, 24, 28, 28 + len(notices)) + bytes(8) + notices + bytes(4)
    archive = struct.pack("<4I4I", 0x33B59, 2, 2048, 0, 1, 6, 2, len(script))
    archive += bytes(2048 - len(archive)) + b"decoy!" + bytes(2048 - 6) + script
    path = tmp_path / "wo2-psp/PSP_GAME/USRDIR/LINKDATA_NA.FNS"
    path.parent.mkdir(parents=True)
    path.write_bytes(archive)
    identity = DonorFile("PSP_GAME/USRDIR/LINKDATA_NA.FNS", "PSP_GAME/USRDIR/LINKDATA_NA.FNS",
                         len(archive), hashlib.sha256(archive).hexdigest())
    monkeypatch.setattr("wozmod.text.resolver.PSP", Donor("fixture", "iso9660", (identity,)))
    assert DonorResolver(tmp_path).resolve(["psp", "fns", 1, "notices", 1]) == "Authored second"


@pytest.mark.skipif(not os.environ.get("WOZMOD_PSP_ISO"), reason="set WOZMOD_PSP_ISO")
def test_real_psp_pointers_do_not_decode_raw_quotes_or_leak_voice_suffixes(tmp_path):
    image = Path(os.environ["WOZMOD_PSP_ISO"])
    if not image.is_file():
        pytest.skip("PSP image absent")
    member_name = "PSP_GAME/USRDIR/LINKDATA_NA.FNS"
    path = extract_donor(image, PSP, tmp_path / "wo2-psp", files=[member_name])[0]
    archive = path.read_bytes()
    payload = json.loads((Path(__file__).resolve().parents[1] / "data/text/table.json").read_text())
    refs = [row["ref"] for row in payload["rows"] if row.get("ref", [])[:2] == ["psp", "fns"]]
    assert refs, "No PSP data-file pointers"
    expected, views = {}, {}
    for ref in refs:
        _, _, member, kind, *indices = ref
        if (member, kind) not in views:
            sector, size = struct.unpack_from("<II", archive, 16 + 8 * member)
            raw = archive[2048 * sector:2048 * sector + size]
            raw = raw if member == 27 else decoded_member(raw)
            views[member, kind] = (
                getattr(parse_script(raw), kind).records if kind in ("notices", "dialog") else
                mesc_blocks(raw) if kind == "mesc" else
                quote_tables(raw) if kind == "quotes" else caption_cues(raw)
            )
        view = views[member, kind]
        if kind in ("notices", "dialog"):
            raw = view[indices[0]].text_bytes
        elif kind == "mesc":
            raw = view[indices[0]][indices[1]]
        elif kind == "quotes":
            raw = re.sub(rb"@(?:SV[0-9]+_[0-9]+|[0-9]+)$", b"", view[indices[0]][indices[1]][2:])
        else:
            raw = b" ".join(word for slot in indices[0] for word in view[slot][2].split())
        expected[json.dumps(ref)] = donor_text(raw)
    donors = DonorResolver(tmp_path)
    actual = {json.dumps(ref): donors.resolve(ref) for ref in refs}
    assert actual == expected

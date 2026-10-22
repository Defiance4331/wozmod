import hashlib
import struct

from wozmod.formats.audio import build_synthetic_kovs_record
from wozmod.voices import build_voice_banks
from synthetic import script, spans, strings, write_ens, write_ps2_archive, write_stock_archive


def _quotes(order):
    officers = spans([strings(order=order)], order=order)
    return spans([strings(order=order), strings(order=order), officers], struct.pack(f"{order}I", 3), order)


def _script(order):
    return script(strings(order=order), strings(order=order), order)


def test_builder_reads_extracted_donor_folders_and_writes_both_sparse_banks_under_mod_voice(tmp_path):
    stock, donors, output = tmp_path / "stock", tmp_path / "donors", tmp_path / "mod"
    one, two, psp = donors / "wo1-x360", donors / "wo2-x360", donors / "wo2-psp/PSP_GAME/USRDIR"
    for folder in (stock, one, two, psp / "sound", stock / "data/Sound/Voice"):
        folder.mkdir(parents=True, exist_ok=True)
    record = build_synthetic_kovs_record(96, sample_count=500)
    (stock / "data/Sound/Voice/Voice.bin").write_bytes(record)
    voiceidx = bytes(8) + struct.pack(">II", 0, 96) * 22665
    se_header = b"_HBW0000" + struct.pack("<IHHI", 20, 0x3200, 0, 0)
    seidx = struct.pack(">II", 244, 0) + (struct.pack(">II", 0, 20) + struct.pack(">II", 20, 0)) * 122
    write_stock_archive(stock, "LINKDATA1", {4056: seidx, 4057: se_header, 4058: voiceidx, 4084: _quotes("<")}, 4085)
    events = {o: b"no lip blocks" for o in range(177, 402)}
    def dream(notices, lines):  # a Z Dream stage with the line counts the PSP pairing expects
        return script(strings(*[b"32Fixture"] * notices), strings(*[b"00Fixture@0001"] * lines))

    zdream = {o: dream(56 if o == 142 else 1, 76 if o == 139 else 1) for o in range(132, 144)}
    write_stock_archive(stock, "LINKDATA2", {**{o: _script("<") for o in range(40, 68)}, **zdream, **events}, 402)
    write_ps2_archive(psp / "LINKDATA_NA.FNS", {27: _quotes("<"), **{
        o + 138: dream(49 if o == 142 else 1, 64 if o == 139 else 1) for o in range(132, 144)}}, 282)
    write_ps2_archive(psp / "sound/voice_na.bns", dict.fromkeys(range(1420), b"RIFF fixture"))
    write_ens(one, {21: _quotes(">"), **{o: _script(">") for o in range(88, 148)},
               **{o: events[177] for o in range(149, 367)}}, 367)
    write_ens(two, {17: _quotes(">"), **{o: _script(">") for o in range(103, 171)},
               **{o: events[177] for o in range(176, 238)}}, 238)
    placeholder = b"Fixture"
    hdx = b"SDsdVers" + bytes(8) + b"SDsdSmpl" + struct.pack("<II", 0, 1)
    hdx += struct.pack("<18I", 0, *([0] * 12), 48000, 0, 0, 0, 2)
    for folder, records, banks in ((one, 1473, 181), (two, 1644, 217)):  # every voice record a placeholder, as in the PSP bank
        (folder / "linkdata_ans.idx").write_bytes(struct.pack(">II", records, 0) + b"".join(
            struct.pack(">5I", i, i, 1, len(placeholder), 48000) for i in range(records)))
        (folder / "link_vodat.bdx").write_bytes((placeholder + bytes(2048 - len(placeholder))) * records)
        (folder / "link_sebank.hdx").write_bytes((hdx + bytes(2048 - len(hdx))) * banks)
        (folder / "link_sebank.bdx").write_bytes(bytes(2 * banks))
    before = {p: hashlib.sha256(p.read_bytes()).digest() for p in tmp_path.rglob("*") if p.is_file()}

    result = build_voice_banks(stock, donors, output)

    assert {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()} == {
        "voice/Voice.bin": struct.pack("<I", 22665) + bytes(181324),
        "voice/SE.bin": struct.pack("<I", 122) + bytes(1964),
        "voice/Lips.bin": struct.pack("<I", 22665) + bytes(8 * 22665),
    }
    assert (result.voice, result.se) == (output / "voice/Voice.bin", output / "voice/SE.bin")
    assert (result.dialogue_tiers, result.attack_tiers, result.japanese_fallback, result.english_se_banks,
            result.lip_tiers) == (
        {"wo1-x360": 0, "wo2-x360": 0, "wo2-psp": 0}, {"wo1-x360": 0, "wo2-x360": 0}, 22665, 0,
        {"donor": 0, "stretched": 0},
    )
    assert all(hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in before.items())

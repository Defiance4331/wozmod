import hashlib
import struct

from wozmod.formats.audio import build_synthetic_kovs_record
from wozmod.voices import build_voice_banks


def _strings(lines, order):
    body, offsets = b"", []
    for line in lines:
        offsets.append(4 + 4 * len(lines) + len(body))
        body += line + b"\0"
    return struct.pack(f"{order}{len(lines) + 1}I", len(lines), *offsets) + body


def _spans(parts, order, head=b""):
    at, spans = len(head) + 8 * len(parts), b""
    for part in parts:
        spans += struct.pack(f"{order}II", at, len(part))
        at += len(part)
    return head + spans + b"".join(parts)


def _quotes(order):
    officers = _spans([_strings([], order)], order)
    return _spans([_strings([], order), _strings([], order), officers], order,
                  struct.pack(f"{order}I", 3))


def _script(order):
    return struct.pack(f"{order}5I", 4, 20, 24, 28, 32) + b"HEADTAIL" + bytes(8)


def _stock_archive(folder, stem, values, count):
    index, body = b"", b""
    for ordinal in range(count):
        value = values.get(ordinal, b"")
        index += struct.pack("<3I", len(value), 0, 0)
        body += value + bytes(-len(value) % 2048)
    (folder / (stem + ".IDX")).write_bytes(index)
    (folder / (stem + ".BIN")).write_bytes(body)


def _ens(folder, values, count):
    index, body, sector = b"", b"", 0
    for ordinal in range(count):
        value = values.get(ordinal, b"")
        allocation = -(-len(value) // 2048)
        index += struct.pack(">4I", sector, allocation, len(value), 0)
        body += value + bytes(-len(value) % 2048)
        sector += allocation
    (folder / "linkdata_ens.idx").write_bytes(struct.pack(">4sIII", b"SM4L", count, sector, 0) + index)
    (folder / "linkdata_ens.lnk").write_bytes(body)


def test_builder_reads_extracted_donor_folders_and_writes_both_sparse_banks_under_mod_voice(tmp_path):
    stock, donors, output = tmp_path / "stock", tmp_path / "donors", tmp_path / "mod"
    one, two = donors / "wo1-x360", donors / "wo2-x360"
    for folder in (stock, one, two, stock / "data/Sound/Voice"):
        folder.mkdir(parents=True, exist_ok=True)
    record = build_synthetic_kovs_record(96, sample_count=500)
    (stock / "data/Sound/Voice/Voice.bin").write_bytes(record)
    voiceidx = bytes(8) + struct.pack(">II", 0, 96) * 22665
    se_header = b"_HBW0000" + struct.pack("<IHHI", 20, 0x3200, 0, 0)
    seidx = struct.pack(">II", 244, 0) + (struct.pack(">II", 0, 20) + struct.pack(">II", 20, 0)) * 122
    _stock_archive(stock, "LINKDATA1", {4056: seidx, 4057: se_header, 4058: voiceidx, 4084: _quotes("<")}, 4085)
    events = {o: b"no lip blocks" for o in range(177, 402)}
    _stock_archive(stock, "LINKDATA2", {**{o: _script("<") for o in range(40, 68)}, **events}, 402)
    _ens(one, {21: _quotes(">"), **{o: _script(">") for o in range(88, 148)},
               **{o: events[177] for o in range(149, 367)}}, 367)
    _ens(two, {17: _quotes(">"), **{o: _script(">") for o in range(103, 171)},
               **{o: events[177] for o in range(176, 238)}}, 238)
    placeholder = b"Fixture"
    hdx = b"SDsdVers" + bytes(8) + b"SDsdSmpl" + struct.pack("<II", 0, 1)
    hdx += struct.pack("<18I", 0, *([0] * 12), 48000, 0, 0, 0, 2)
    for folder, records, banks in ((one, 1473, 181), (two, 1644, 217)):  # every voice record a placeholder
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
        {"wo1-x360": 0, "wo2-x360": 0}, {"wo1-x360": 0, "wo2-x360": 0}, 22665, 0, {"donor": 0, "stretched": 0},
    )
    assert all(hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in before.items())

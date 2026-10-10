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
    one, two = donors / "wo1", donors / "wo2-x360"
    for folder in (stock, one, two, stock / "data/Sound/Voice"):
        folder.mkdir(parents=True, exist_ok=True)
    record = build_synthetic_kovs_record(96, sample_count=500)
    (stock / "data/Sound/Voice/Voice.bin").write_bytes(record)
    voiceidx = bytes(8) + struct.pack(">II", 0, 96) * 22665
    se_header = b"_HBW0000" + struct.pack("<IHHI", 20, 0x3200, 0, 0)
    seidx = struct.pack(">II", 244, 0) + (struct.pack(">II", 0, 20) + struct.pack(">II", 20, 0)) * 122
    _stock_archive(stock, "LINKDATA1", {4056: seidx, 4057: se_header, 4058: voiceidx, 4084: _quotes("<")}, 4085)
    _stock_archive(stock, "LINKDATA2", {o: _script("<") for o in range(40, 68)}, 68)
    _ens(one, {512: _quotes("<"), **{o: _script("<") for o in range(579, 639)}}, 639)
    _ens(two, {17: _quotes(">"), **{o: _script(">") for o in range(103, 171)}}, 171)
    (one / "linkdata_ans.idx").write_bytes(struct.pack("<4sIII", b"SM4L", 1468, 1, 0) + struct.pack("<4I", 0, 1, 96, 0) * 1468)
    (one / "link_vodat.bdx").write_bytes(record + bytes(2048 - len(record)))
    placeholder = b"Fixture"
    (two / "linkdata_ans.idx").write_bytes(struct.pack(">II", 1644, 0) + b"".join(
        struct.pack(">5I", i, i, 1, len(placeholder), 48000) for i in range(1644)))
    (two / "link_vodat.bdx").write_bytes((placeholder + bytes(2048 - len(placeholder))) * 1644)
    wo1_hdx = struct.pack("<HHII", 2, 1, 2, 12) + struct.pack("<IHH8xIII12x", 0, 48000, 0, 1, 0, 2)
    (one / "link_sebank.hdx").write_bytes(wo1_hdx + bytes(2048 - len(wo1_hdx)))
    (one / "link_sebank.bdx").write_bytes(bytes(2))
    wo2_hdx = b"SDsdVers" + bytes(8) + b"SDsdSmpl" + struct.pack("<II", 0, 1)
    wo2_hdx += struct.pack("<18I", 0, *([0] * 12), 48000, 0, 0, 0, 2)
    (two / "link_sebank.hdx").write_bytes((wo2_hdx + bytes(2048 - len(wo2_hdx))) * 217)
    (two / "link_sebank.bdx").write_bytes(bytes(434))
    before = {p: hashlib.sha256(p.read_bytes()).digest() for p in tmp_path.rglob("*") if p.is_file()}

    result = build_voice_banks(stock, donors, output)

    assert {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()} == {
        "voice/Voice.bin": struct.pack("<I", 22665) + bytes(181324),
        "voice/SE.bin": struct.pack("<I", 122) + bytes(1964),
    }
    assert (result.voice, result.se) == (output / "voice/Voice.bin", output / "voice/SE.bin")
    assert (result.dialogue_tiers, result.attack_tiers, result.japanese_fallback, result.english_se_banks) == (
        {"wo1-pc": 0, "wo2-x360": 0}, {"wo1-pc": 0, "wo2-x360": 0}, 22665, 0,
    )
    assert all(hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in before.items())

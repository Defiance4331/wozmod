import hashlib
import struct

import pytest

from wozmod.discs import DiscError
from wozmod.donors import Donor, DonorFile, check_donor_image, extract_donor


def _disc(tmp_path, content=b"authored"):
    image = bytearray(35 * 2048)
    descriptor = memoryview(image)[32 * 2048:33 * 2048]
    descriptor[:20] = descriptor[-20:] = b"MICROSOFT*XBOX*MEDIA"
    struct.pack_into("<II", descriptor, 20, 33, 2048)
    name = b"item"
    record = struct.pack("<HHIIBB", 0, 0, 34, len(content), 0x20, len(name)) + name
    image[33 * 2048:33 * 2048 + len(record)] = record
    image[34 * 2048:34 * 2048 + len(content)] = content
    path = tmp_path / "image"
    path.write_bytes(image)
    return path


def _donor(path="nested/result", content=b"authored"):
    return Donor("synthetic", "xdvdfs", (
        DonorFile(path, "item", len(content), hashlib.sha256(content).hexdigest()),
    ))


def _iso(tmp_path):
    image = bytearray(22 * 2048)
    def both(value, width):
        return value.to_bytes(width, "little") + value.to_bytes(width, "big")
    def record(name, sector, size, flags=0):
        result = bytearray(33 + len(name) + (len(name) % 2 == 0))
        result[0] = len(result)
        result[2:10] = both(sector, 4)
        result[10:18] = both(size, 4)
        result[25] = flags
        result[28:32] = both(1, 2)
        result[32] = len(name)
        result[33:33 + len(name)] = name
        return result
    descriptor = memoryview(image)[16 * 2048:17 * 2048]
    descriptor[:7] = b"\x01CD001\x01"
    descriptor[80:88] = both(22, 4)
    descriptor[120:124] = both(1, 2)
    descriptor[128:132] = both(2048, 2)
    root = record(b"\0", 20, 2048, 2)
    descriptor[156:156 + len(root)] = root
    image[17 * 2048:17 * 2048 + 7] = b"\xffCD001\x01"
    entry = record(b"ITEM;1", 21, 8)
    image[20 * 2048:20 * 2048 + len(entry)] = entry
    image[21 * 2048:21 * 2048 + 8] = b"authored"
    path = tmp_path / "image"
    path.write_bytes(image)
    return path


def test_source_member_is_written_to_its_manifest_destination(tmp_path):
    destination = tmp_path / "output"
    outputs = extract_donor(_disc(tmp_path), _donor(), destination)
    assert outputs == (destination / "nested/result",)
    assert {p.relative_to(destination).as_posix(): p.read_bytes()
            for p in destination.rglob("*") if p.is_file()} == {"nested/result": b"authored"}


def test_two_slices_of_one_disc_file_are_written_from_their_offsets(tmp_path):
    # Bug: a slice is copied from the file's start, or the whole-file size check refuses a slice of a longer file.
    image = _disc(tmp_path, b"0123456789")
    slices = {"first.bin": (1, b"12"), "second.bin": (6, b"678")}
    donor = Donor("synthetic", "xdvdfs", tuple(
        DonorFile(name, "item", len(data), hashlib.sha256(data).hexdigest(), offset)
        for name, (offset, data) in slices.items()))
    destination = tmp_path / "output"
    check_donor_image(image, donor)
    extract_donor(image, donor, destination)
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == {"first.bin": b"12", "second.bin": b"678"}


def test_casefolded_iso_member_lookup_opens_the_actual_disc_spelling(tmp_path):
    donor = Donor("synthetic", "iso9660", _donor().files)
    outputs = extract_donor(_iso(tmp_path), donor, tmp_path / "output")
    assert [path.read_bytes() for path in outputs] == [b"authored"]


def test_same_size_wrong_hash_does_not_replace_an_existing_output(tmp_path):
    destination = tmp_path / "output"
    existing = destination / "nested/result"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"previous")
    with pytest.raises(DiscError, match="hash"):
        extract_donor(_disc(tmp_path, b"tampered"), _donor(), destination)
    assert existing.read_bytes() == b"previous"


@pytest.mark.parametrize("name", ["../outside", "/outside", "nested/../outside", "nested\\outside"])
def test_manifest_parent_component_cannot_write_outside_destination(name):
    with pytest.raises(ValueError, match="relative"):
        _donor(name)


def test_output_parent_symlink_cannot_redirect_an_extracted_member(tmp_path):
    destination = tmp_path / "output"
    destination.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (destination / "nested").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        extract_donor(_disc(tmp_path), _donor(), destination)
    assert list(outside.iterdir()) == []

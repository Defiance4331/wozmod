import io

import pytest

from wozmod.discs.common import DiscError, ExtentStream


def test_seek_at_extent_boundary_does_not_read_gap_bytes(tmp_path):
    source = tmp_path / "image"
    source.write_bytes(b"ABgapCDEtail")
    with io.BufferedReader(ExtentStream(source, ((0, 2), (5, 3)))) as stream:
        assert stream.read(3) == b"ABC"
        assert stream.seek(2) == 2
        assert stream.read() == b"CDE"
        assert stream.seek(-2, 2) == 3
        assert stream.read(20) == b"DE"
        assert stream.seek(20) == 20
        assert stream.read() == b""


def test_extent_past_image_end_is_rejected_instead_of_silent_short_read(tmp_path):
    source = tmp_path / "image"
    source.write_bytes(b"AB")
    with pytest.raises(DiscError, match="outside"):
        ExtentStream(source, ((1, 2),))

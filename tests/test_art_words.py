from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wozmod.art import render, words


def test_letter_map_rebuilds_each_label_and_refuses_a_shifted_box(tmp_path):
    # Bug: a space lands after the wrong letter, an unlabelled line gains text, or a letter
    # moved to another box still gives a word (the fingerprint must catch it).
    labels = ["Ab  c", None, "dA"]
    row = {"donor": [1, 2, 0],
           "letters": {"A": "0:0 2:1", "b": "0:1", "c": "0:2", "d": "2:0"},
           "texts": [{"boxes": 3, "spaces": [1, 1], "sha256": words.sha256(labels[0])}, None,
                     {"boxes": 2, "sha256": words.sha256(labels[2])}]}
    path = tmp_path / "art.json"
    path.write_text(json.dumps({"faces": {"f": {"rows": [row]}}}))
    assert words.load(path)["faces"]["f"]["rows"][0] == {"donor": [1, 2, 0], "texts": labels}

    row["letters"] |= {"A": "0:0 2:0", "d": "2:1"}
    path.write_text(json.dumps({"faces": {"f": {"rows": [row]}}}))
    with pytest.raises(ValueError, match="line 2 does not give the word"):
        words.load(path)


def test_pointer_deletes_then_cases_and_must_give_its_word(tmp_path, monkeypatch):
    # Bug: an edit keeps the deleted span instead of the text around it, or a pointer whose
    # donor string moved still resolves to some other text.
    class Donors:
        def resolve(self, ref):
            return {7: "\tAlder Grove", 8: "Harbors"}[ref[2]]
    monkeypatch.setattr(words, "DONORS", Donors())
    path = tmp_path / "visuals.json"
    stored = {"a": {"ref": ["wo2-x360", "pe", 7], "edits": [[0, 1, ""]], "case": "upper",
                    "sha256": words.sha256("ALDER GROVE")},
              "b": [{"ref": ["wo2-x360", "pe", 8], "edits": [[0, 1, ""], [6, 7, ""]], "sha256": words.sha256("arbor")}]}
    path.write_text(json.dumps(stored))
    assert words.load(path) == {"a": "ALDER GROVE", "b": ["arbor"]}

    stored["a"]["ref"][2] = 8
    path.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="does not give the word"):
        words.load(path)


@pytest.mark.skipif(not os.environ.get("WOZMOD_DONORS"), reason="set WOZMOD_DONORS to extracted donor folders")
def test_every_lettering_text_resolves_from_the_donors():
    # Bug: a pointer whose offset, edit or case drifted, or a letter map that lost a letter, so a
    # recipe text is no longer found (or its fingerprint no longer matches) and the menus can't be built.
    words.use(Path(os.environ["WOZMOD_DONORS"]))
    try:
        requests, _, _ = render._load_requests()
    finally:
        words.use(None)
    assert requests

"""The disc table: each disc's donor, program and pins belong to the same disc."""

import json

from wozmod.donors import DISCS

PLATFORM = {"xdvdfs": "x360", "iso9660": "psp"}  # the decryption program's platform


def test_each_discs_program_pins_belong_to_its_donor():
    """Bug: a disc is paired with another disc's donor or pins, so its folder, image check and program come from
    two different discs."""
    wrong = []
    for key, disc in DISCS.items():
        program = disc.program
        if {program.plain.platform, program.encrypted.platform} != {PLATFORM[disc.donor.format]}:
            wrong.append(f"{key}: pins for another platform")
        for listed in (file for file in disc.donor.files if file.source == program.member):
            if (listed.size, listed.sha256) != (program.encrypted.size, program.encrypted.sha256):
                wrong.append(f"{key}: its donor pins another {program.member}")
    assert wrong == []


def test_every_data_loader_refuses_a_disc_name_that_is_not_a_disc_key(tmp_path, monkeypatch):
    """Bug: a pointer or donor in data/ keeps a retired or misspelt disc name (the short "x360" and "psp" of
    v0.1.0), and its loader takes it: code that compares names (`== "wo2-x360"`) then matches nothing and falls
    back quietly, or the install stops at that row minutes in. Each loader is given one stale name."""
    from wozmod.art import words
    from wozmod.fonts import menu_references
    from wozmod.text.build import _load_data

    key = "0" * 64

    def text(row):
        (tmp_path / "table.json").write_text(json.dumps({"version": 1, "rows": [{"key": key, **row}], "name_parts": []}))
        _load_data(tmp_path / "table.json")

    def art(value):
        (tmp_path / "art.json").write_text(json.dumps({"faces": {}, "textures": [value]}))
        words.load(tmp_path / "art.json")

    def catalog(row):
        (tmp_path / "catalog.json").write_text(json.dumps({"schema": "moz-menu-visual-references-v1",
                                                           "textures": {"3.2": {"rows": {"7": row}}}}))
        monkeypatch.setattr(menu_references, "CATALOG", tmp_path / "catalog.json")
        menu_references.load_catalog.cache_clear()
        try:
            menu_references.load_catalog()
        finally:
            menu_references.load_catalog.cache_clear()

    cases = {
        "a text row's pointer": (text, {"source": "wo2-x360", "ref": ["x360", "pe", 0]}, "x360"),
        "a pointer an edit inserts": (text, {"source": "edited", "ref": ["wo1-x360", "pe", 0],
                                             "edits": [[0, 1, ["psp", "prx", 0]]]}, "psp"),
        "an art word's pointer": (art, {"text": {"ref": ["x360", "pe", 0], "sha256": key}}, "x360"),
        "a catalog word's pointer": (catalog, {"pc_text": {"ref": ["psp", "prx", 0], "sha256": key}}, "psp"),
        "a catalog original's donor": (catalog, {"original": {"donor": "x360", "kind": "official",
                                                              "ens": [7, 2, 0], "crop": [0, 0, 1, 1]}}, "x360"),
    }
    taken = []
    for case, (load, value, name) in cases.items():
        try:
            load(value)
            taken.append(case)
        except ValueError as error:
            if repr(name) not in str(error):
                taken.append(f"{case}: {error}")
    assert taken == []

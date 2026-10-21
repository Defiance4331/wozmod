"""The disc table: each disc's donor, program and pins belong to the same disc."""

from wozmod.donors import DISCS

PLATFORM = {"xdvdfs": "x360", "iso9660": "psp"}


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

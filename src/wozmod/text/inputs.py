"""Additional disc member required by text pointers."""

from pathlib import Path

from wozmod.donors import Donor, DonorFile, extract_donor

WO1_TEXT_PROGRAM = Donor(
    "wo1-text-program", "iso9660", (
        DonorFile(
            "OROCHI.exe",
            "program files/KOEI/WARRIORS_OROCHI/OROCHI_WIN/OROCHI.exe",
            4310048,
            "387c45a2631ba0ee72830fb9ddb90dc813fa19c7764eb932d5f65e234fe28b2b",
        ),
    ),
)


def extract_text_program(image: Path, donor_root: Path) -> Path:
    """Extract the checked WO1 program beside the existing WO1 donors."""
    return extract_donor(image, WO1_TEXT_PROGRAM, donor_root / "wo1")[0]

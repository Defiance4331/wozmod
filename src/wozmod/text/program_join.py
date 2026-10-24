"""Pair each stock program string with the donor string its twin code reads: the code join behind the `pe` and
`prx` rows.

MOZ (OROCHI_Z.exe), the two Xbox 360 programs and the WO2 PSP program read their menu, item, skill and system strings
from pointer tables. An entry's identity is what its reader is given, so each table is joined on that:

- Messages: the getter (MOZ 0x47D060, WO2 X360 0x8219BF90, WO1 X360 0x82185548, WO2 PSP inlined loads from its
  table) takes an id. Each anchor names the instruction that sets the id at a MOZ call and at its twin call in a
  donor, and the ids are read from those instructions. An anchor with a count is an index the code adds to the id,
  one that means the same in both games (an officer, a stage, a Dream stage, an element, a skill, an arena mode). No
  id is inferred between anchors. Other ids sit in tables of message ids the code indexes with a game identity (a
  gallery series group and slot, a movie, a stage in the mission list); those tables are joined on that identity.
- Name parts and weapons: each entry is a (name, reading) pair, and the Xbox 360 readers sort and look up by the
  Japanese reading, which those donors keep. The join is the reading. The PSP keeps no readings (every reading field
  is one empty string) and holds MOZ's 890 names and 384 weapons; its readers index them by the same id.
- Small tables the readers index with the same game id in both games (the officer's ability, the key action, the
  result label, the skill, the treasure, the army, the base, the battle, the music track): the same index, over the
  entries both games have. MOZ appends its own entries to these tables, past the donor's count; where those entries
  are another donor's, an offset table names the code that maps them.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from wozmod.formats import pe
from wozmod.text.psp_exe import PSP_STRING

DONORS = ("wo1-x360", "wo2-x360", "wo2-psp")  # the text donor order of AGENTS.md
X360 = DONORS[:2]
X360_BASE = 0x82000000  # a decrypted default.pe is its own memory image from this address


@dataclass(frozen=True)
class Table:
    va: int
    count: int
    stride: int = 4  # 8 for (name, reading) pairs


@dataclass(frozen=True)
class Anchor:
    """A getter call in MOZ and its twin in a donor, each given by the instruction that sets the message id."""
    moz: int
    donor: int
    count: int = 1  # ids the code reaches from there with one index
    step: int = 1  # the index's step between ids
    donor_scale: int = 1  # 2 where the donor doubles the immediate (addi then slwi 1) before the call
    first: int = 0  # the first index the anchor covers


class Program:
    """A donor program: its words and immediates by address, and the text-table pointer of a string at an address."""

    def __init__(self, donor: str, data: bytes):
        self.donor, self.data = donor, data
        if donor == "wo2-psp":
            # The PRX has one loaded segment: e_phoff at 0x1C, then p_offset and p_vaddr. Its pointers are segment
            # addresses (each table word below carries an R_MIPS_32 relocation).
            phoff = struct.unpack_from("<I", data, 0x1C)[0]
            offset, vaddr = struct.unpack_from("<II", data, phoff + 4)
            self.base, self.order = vaddr - offset, "<"
            self.slots = {match.start(): n for n, match in enumerate(PSP_STRING.finditer(data))}
        else:
            self.base, self.order = X360_BASE, ">"

    def word(self, va: int) -> int:
        return struct.unpack_from(self.order + "I", self.data, va - self.base)[0]

    def ref(self, va: int) -> list | None:
        """The table.json pointer to the string at va: a program offset, or the PSP string slot."""
        at = va - self.base
        if self.donor == "wo2-psp":
            return [self.donor, "prx", self.slots[at]] if at in self.slots else None
        return [self.donor, "pe", at]

    def immediate(self, va: int) -> int:
        """The immediate of li or addi (PPC), or addiu (MIPS), at va."""
        word = self.word(va)
        if word >> 26 != (9 if self.donor == "wo2-psp" else 14):
            raise ValueError(f"{self.donor} {va:#x} does not add an immediate")
        return ((word & 0xFFFF) ^ 0x8000) - 0x8000

    def entries(self, table: Table, field: int = 0) -> list[int]:
        """The address each entry of a pointer table points at."""
        return [self.word(table.va + table.stride * i + 4 * field) for i in range(table.count)]


# The message tables. WO2 X360 reads the English table when the language word is 0 (0x8219BFD4). The PSP's runs up
# to its names table; its code reads it from 0x11888 (lui/addiu 0x24234C) or inlines a single entry's load.
MESSAGES = {"moz": Table(0x6DE4F0, 911), "wo2-x360": Table(0x8200FF18, 788), "wo1-x360": Table(0x8200DFB0, 609),
            "wo2-psp": Table(0x24234C, 945)}

# The help bar: MOZ 0x55DE30, WO2 X360 0x822AF4B8 and WO1 X360 0x8226E200 each pass their first argument to the
# getter and lay the string out as the screen's button line. Their callers are paired by the calls around them (the
# same callees with the same immediate arguments before and after), and an anchor is kept only where the two strings
# show the same buttons in the same order (MOZ and WO1 number the button glyphs alike; WO2 swaps the first three).
# A WO1 help anchor goes through the WO2 call given after "via": the MOZ call's WO2 twin, or a WO2 call of the same
# id, whose WO1 twin is the donor instruction.
HELP = {
    "wo2-x360": (
        Anchor(0x56A6F9, 0x822BA37C), Anchor(0x53D198, 0x82296EAC), Anchor(0x53D011, 0x82296D28),  # 59, 60, 61
        Anchor(0x52A32B, 0x82285C64), Anchor(0x529D6E, 0x82285650), Anchor(0x539A17, 0x82293DD4),  # 62, 63, 65
        Anchor(0x5492FE, 0x8229F6B4), Anchor(0x583256, 0x822CC178), Anchor(0x53D1CD, 0x82296ED4),  # 68, 69, 71
        Anchor(0x537594, 0x82291698), Anchor(0x53A7D5, 0x82294604), Anchor(0x53236F, 0x8228D8AC),  # 72, 73, 75
        Anchor(0x53D1B5, 0x82296EC4), Anchor(0x53D37E, 0x82297084), Anchor(0x572E28, 0x822BE940),  # 76, 77, 80
        Anchor(0x56B1FE, 0x822BACD4), Anchor(0x530504, 0x8228C144), Anchor(0x55688C, 0x822A923C),  # 82, 85, 86
        Anchor(0x586D1A, 0x822CEB7C), Anchor(0x539B5F, 0x82293F38), Anchor(0x572DA8, 0x822BE8BC),  # 88, 89, 90
        Anchor(0x580FA2, 0x822CA238), Anchor(0x584A57, 0x822CD4A4), Anchor(0x572E73, 0x822BE984),  # 91, 92, 93
        Anchor(0x536A42, 0x822909C4), Anchor(0x572E50, 0x822BE96C), Anchor(0x5583F6, 0x822AA9F0),  # 97, 98, 99
        Anchor(0x5289B1, 0x82284440), Anchor(0x529F43, 0x82285824), Anchor(0x52FDDE, 0x8228B854),  # 101, 102, 104
        Anchor(0x5A3F63, 0x822E2214), Anchor(0x558003, 0x822AA5B0), Anchor(0x52804C, 0x82283994),  # 108, 109, 111
        Anchor(0x5A3B6B, 0x822E1CBC), Anchor(0x531487, 0x8228CDC4),  # 112, 114
        # Character select: one id per mode, kept in a register (MOZ 0x544390, WO2 0x8229C280).
        Anchor(0x5455DC, 0x8229D344),  # 66 (`add eax,0x42` after the flag test; li r3,0x42)
        # Settings help (MOZ 0x53F0BC, WO2 0x82298CC0): 106, or 107 when the caller passes 1.
        Anchor(0x53F0DE, 0x82298CF8), Anchor(0x53F0EE, 0x82298D08),
    ),
    "wo1-x360": (
        Anchor(0x56A6F9, 0x8217E9C4),  # 59 / 56, via 0x82193028
        Anchor(0x53B416, 0x8225AFA4),  # 60 / 57, via 0x82295610
        Anchor(0x53752F, 0x822574DC),  # 62 / 59, via 0x8229163C
        Anchor(0x529D6E, 0x8225E610),  # 63 / 60, via 0x82298FE8
        Anchor(0x539A17, 0x82259BC8),  # 65 / 62, via 0x82293DD8
        Anchor(0x5492FE, 0x82290EEC),  # 68 / 65, via 0x822DDFE4
        Anchor(0x5848FD, 0x82283378),  # 69 / 66, via 0x822CD35C
        Anchor(0x537594, 0x8225753C),  # 72 / 69, via 0x8229169C
        Anchor(0x53A7D5, 0x8225A45C),  # 73 / 70, via 0x8229460C
        Anchor(0x572E28, 0x8227A428),  # 80 / 77, via 0x822BE944
        Anchor(0x56AE28, 0x82275E88),  # 82 / 79, via 0x822BA8B8
        Anchor(0x52BDB7, 0x8224ED54),  # 85 / 82, via 0x82287728
        Anchor(0x55688C, 0x8226B250),  # 86 / 83, via 0x822A9240
        Anchor(0x586D1A, 0x82284A10),  # 88 / 85, via 0x822CEB80
        Anchor(0x539B5F, 0x82259D60),  # 89 / 86, via 0x82293F3C
        Anchor(0x572DA8, 0x8227A3A4),  # 90 / 87, via 0x822BE8C0
        Anchor(0x572E73, 0x8227A46C),  # 93 / 90, via 0x822BE988
        Anchor(0x536A42, 0x8226D490),  # 97 / 94, via 0x822ADED8
        Anchor(0x572E50, 0x8227A454),  # 98 / 95, via 0x822BE970
    ),
}

# Twin getter calls. Comments give the stock id, then the donor id, as the instructions read today.
OFFICERS = {"wo2-x360": 92, "wo1-x360": 79, "wo2-psp": 96}  # officer-indexed blocks: the officers each block holds
ANCHORS = {
    "wo2-x360": (
        # An objective line (sc_ubase), 154 + i at 0x4F9158 / 0x821C193C. i is word 1 of script command 0x2C (MOZ
        # 0x4F2C6C, WO2 0x821BCD5C); twin stages give equal codes for every i they use, and the 63 templates split
        # at 0x1F and 0x3F alike.
        Anchor(0x4F9151, 0x821C1938, 63),
        Anchor(0x49E970, 0x822EA0B4),  # 218 / 218 (ev_config)
        Anchor(0x55482D, 0x822A88F0),  # 229 / 229 (gallery)
        Anchor(0x5829CF, 0x822CB8B4),  # 228 / 228
        Anchor(0x5832C6, 0x822CC1F4),  # 232 / 232
        Anchor(0x58268E, 0x822CB62C),  # 227 / 227: the result screen's message queue (MOZ 0x585150, WO2 0x822CDC98)
        Anchor(0x5A3F4B, 0x822E21FC),  # 237 / 235 (MOZ 0x5A2BD0, WO2 0x822E1A38 pass it to the getter)
        # 238 + s / 236 + s: stage s's objective (MOZ 0x5877C0, WO2 0x822CFA48; stage s is the same stage in both
        # for s < 40, the WO2 stages: MOZ 0x4F06D0 and WO2 0x821B9BF0 load stage s's script, paired by voices).
        Anchor(0x5877F5, 0x822CFAA4, 40),
        Anchor(0x547656, 0x8229E390, 28),  # 278 + k / 296 + k: a Dream stage's summary, k < 28 (MOZ 0x547647)
        Anchor(0x59375B, 0x822D7498, OFFICERS["wo2-x360"], 2, 2),  # 378 + 2k / 324 + 2k: officer k's attack, line 1
        Anchor(0x5937C1, 0x822D74F8, OFFICERS["wo2-x360"], 2),  # 379 + 2k / 325 + 2k: its line 2
        Anchor(0x5937E7, 0x822D7514),  # 570 / 508
        Anchor(0x593709, 0x822D7430),  # 572 / 510
        Anchor(0x593594, 0x822DA1F0),  # 574 / 512
        Anchor(0x5935E8, 0x822DA29C),  # 575 / 513
        Anchor(0x593638, 0x822DA348),  # 576 / 514
        Anchor(0x593509, 0x822DA00C),  # 577 / 515
        Anchor(0x593B5A, 0x822DB2DC, 9),  # 578 + b / 516 + b: an ability's condition (0x593B20 / 0x822DB270)
        Anchor(0x593B3A, 0x822DB2C0, 9),  # 587 + b / 525 + b: the same with no time limit
        Anchor(0x59736D, 0x822DAFAC),  # 597 / 535: mission status
        Anchor(0x596F6D, 0x822DAC6C),  # 598 / 536
        Anchor(0x5970CA, 0x822DADA8),  # 599 / 537
        Anchor(0x597205, 0x822DAE94),  # 600 / 538
        Anchor(0x59732D, 0x822DAF64),  # 601 / 539
        Anchor(0x597452, 0x822DB09C),  # 602 / 540
        Anchor(0x535A1C, 0x82298BF4),  # 605 / 543: player 2 joins
        # 678 + e / 592 + e: element e's effect. e is a fusion list entry's id (MOZ 0x52DBF0, WO2 0x82289290: each set
        # bit of the weapon's attribute mask, then 15 for level and 16 for slots, costed from the same 17-entry table
        # MOZ 0x6D6180 / WO2 0x82008D56), the entry that also names the element (767 + e below); 17 is 695 / 609.
        Anchor(0x5955F7, 0x822D8CAC, 18),
        Anchor(0x56A5F6, 0x822BA248),  # 695 / 609
        Anchor(0x56B1D2, 0x822BACB0),  # 696 / 610 (MOZ 0x56A6B0, WO2 0x822BA320 pass it to the getter)
        Anchor(0x56B1E6, 0x822BACC4),  # 697 / 611
        Anchor(0x556BF6, 0x822A9638),  # 708 / 622: gallery background
        Anchor(0x556CDF, 0x822AABCC),  # 709 / 623
        Anchor(0x556EE9, 0x822A98B0),  # 712 / 626
        Anchor(0x52ED57, 0x8228A75C),  # 750 / 655: weapon fusion (barrack)
        Anchor(0x52F0B9, 0x8228AA80),  # 751 / 656
        Anchor(0x52F5A8, 0x8228AF00),  # 752 / 657
        Anchor(0x52FEA7, 0x8228B928),  # 753 / 658
        Anchor(0x52FF31, 0x8228B9C4),  # 754 / 659
        Anchor(0x52FEE6, 0x8228B974),  # 755 / 660
        Anchor(0x530314, 0x8228BE90),  # 756 / 661
        Anchor(0x53030D, 0x8228BE84),  # 760 / 665: the other branch of 756's slot test
        Anchor(0x52FFD0, 0x8228BA74),  # 757 / 662
        Anchor(0x52801B, 0x82283928),  # 758 / 663: weapon upgrade, the same count test as 759
        Anchor(0x528038, 0x82283980),  # 759 / 664
        Anchor(0x52836D, 0x82283D9C),  # 761 / 666
        Anchor(0x5288C1, 0x82284320),  # 762 / 667
        Anchor(0x527E91, 0x82283768),  # 763 / 668
        Anchor(0x529CAA, 0x82285560),  # 764 / 669
        Anchor(0x529C74, 0x82285510),  # 765 / 670: the selected skill's mask test, beside 764
        Anchor(0x52935B, 0x82285008),  # 766 / 671
        Anchor(0x52E77B, 0x8228A0CC, 18),  # 767 + e / 672 + e: an element's name, e < 18 (both list 18 elements)
        # 815 + t / 720 + t: skill t's effect; t also indexes the 21-byte skill records MOZ 0x6D61B6 / WO2 0x82008D8C,
        # whose 15 entries are the same in both.
        Anchor(0x526BC8, 0x822824C8, 15),
        # 830 + c / 735 + c: c is byte 3 of officer x's record (MOZ 0x6D4B10, WO2 0x82007A10; the first 92 are the
        # same); 835 + k / 740 + k: k is byte 0 of record c of MOZ 0x6D61A2 / WO2 0x82008D78 (five, the same).
        Anchor(0x59810C, 0x822DBA4C, 5),
        Anchor(0x59814E, 0x822DBAD4, 3),
        # Arena rules by mode m: both builders (MOZ 0x5A7300, WO2 0x822E4630) list the modes 0, 1, 2, 3.
        Anchor(0x5A6755, 0x822E4058, 4),  # 838 + m / 743 + m
        Anchor(0x5A67E4, 0x822E4110, 4),  # 842 + m / 747 + m
        Anchor(0x5A6851, 0x822E41B8, 4),  # 846 + m / 751 + m
        Anchor(0x573EC8, 0x822BF92C),  # 850 / 755: loading
        *HELP["wo2-x360"],
    ),
    "wo1-x360": (
        # 154 + i / 151 + i (WO1 0x821A4B28, from command 0x2C at 0x821A0934). WO1 has no template for i 13, 14 and
        # 44-48: its table holds the filler string there, and WO2 added those templates. 15-30 and 49-62 are filler
        # in all three.
        Anchor(0x4F9151, 0x821A4C74, 13),
        Anchor(0x4F9151, 0x821A4C74, 13, first=31),
        Anchor(0x49E970, 0x82298840),  # 218 / 215
        Anchor(0x58268E, 0x82281CB8),  # 227 / 222: the message queue (WO1 0x82283C78), via WO2 0x822CB630
        Anchor(0x58307A, 0x822823B4),  # 231 / 226: via WO2 0x822CC004, which WO2 changed to its id 233
        Anchor(0x59375B, 0x8228BA88, OFFICERS["wo1-x360"], 2, 2),  # 378 + 2k / 288 + 2k
        Anchor(0x5937C1, 0x8228BAE8, OFFICERS["wo1-x360"], 2),  # 379 + 2k / 289 + 2k
        Anchor(0x5937E7, 0x8228BB04),  # 570 / 446
        Anchor(0x593709, 0x8228BA20),  # 572 / 448
        Anchor(0x593594, 0x8228B868),  # 574 / 450
        Anchor(0x5935E8, 0x8228B8C8),  # 575 / 451
        Anchor(0x593638, 0x8228B928),  # 576 / 452
        Anchor(0x593509, 0x8228B774),  # 577 / 453
        Anchor(0x593B5A, 0x8228EC98, 9),  # 578 + b / 454 + b
        Anchor(0x593B3A, 0x8228EC7C, 9),  # 587 + b / 463 + b
        Anchor(0x59736D, 0x8228E950),  # 597 / 473
        Anchor(0x596F6D, 0x8228E608),  # 598 / 474
        Anchor(0x5970CA, 0x8228E748),  # 599 / 475
        Anchor(0x597205, 0x8228E838),  # 600 / 476
        Anchor(0x59732D, 0x8228E908),  # 601 / 477
        Anchor(0x597452, 0x8228EA44),  # 602 / 478
        Anchor(0x535A1C, 0x8225E294),  # 605 / 481
        # 678 + e / 506 + e: the element effect read by WO1's fusion screen 0x82251280 (its card 0x8228CF28 reads the
        # same at 0x8228D204, from the attribute mask at record + 2).
        Anchor(0x5955F7, 0x822512A4, 18),
        # Weapon fusion: WO1's own fusion screen (barrack) reads these; each pairs with the MOZ call of the same role.
        Anchor(0x52ED57, 0x82250398),  # 750 / 578
        Anchor(0x52F0B9, 0x822528A4),  # 751 / 579
        Anchor(0x52F5A8, 0x822535E8),  # 752 / 580
        Anchor(0x52FEA7, 0x82252C50),  # 753 / 581
        Anchor(0x52FF31, 0x82252CAC),  # 754 / 582
        Anchor(0x52FEE6, 0x82252C80),  # 755 / 583
        Anchor(0x530314, 0x82253044),  # 756 / 584
        Anchor(0x52FFD0, 0x82252D5C),  # 757 / 585
        Anchor(0x52E77B, 0x822513B8, 18),  # 767 + e / 586 + e
        *HELP["wo1-x360"],
    ),
    "wo2-psp": (
        # A Dream stage's summary: k is word 2 of the Dream record in both (MOZ 0x547641, PSP 0x20FBE8), split at 28
        # (MOZ 0x547647, PSP 0x20FB38). MOZ shows 278 + k or 338 + k, the PSP 277 + k.
        Anchor(0x547656, 0x20FBF4, 28),
        Anchor(0x54765E, 0x20FBF4, 12, first=28),
        # Officer k's attack lines: the PSP doubles k first (0x80188), so its immediates are the ids.
        Anchor(0x59375B, 0x8018C, OFFICERS["wo2-psp"], 2),  # 378 + 2k / 317 + 2k
        Anchor(0x5937C1, 0x801E8, OFFICERS["wo2-psp"], 2),  # 379 + 2k / 318 + 2k
    ),
}


def _moz_immediate(exe: bytes, layout: dict, va: int) -> int:
    """The immediate of a push, add, mov or lea at ``va`` (the forms that set a getter's id)."""
    at = pe.file_offset(layout, va)
    op, modrm = exe[at], exe[at + 1]
    if op == 0x68:
        return struct.unpack_from("<i", exe, at + 1)[0]
    if op == 0x6A:
        return struct.unpack_from("<b", exe, at + 1)[0]
    if op == 0x05 or 0xB8 <= op <= 0xBF:
        return struct.unpack_from("<i", exe, at + 1)[0]
    if op == 0x81 and modrm >> 3 & 7 == 0 and modrm >> 6 == 3:
        return struct.unpack_from("<i", exe, at + 2)[0]
    if op == 0x83 and modrm >> 3 & 7 == 0 and modrm >> 6 == 3:
        return struct.unpack_from("<b", exe, at + 2)[0]
    if op == 0x8D and modrm >> 6 in (1, 2):  # lea reg, [base (+ index) + displacement]: the displacement
        return struct.unpack_from("<b" if modrm >> 6 == 1 else "<i", exe, at + (3 if modrm & 7 == 4 else 2))[0]
    raise ValueError(f"MOZ {va:#x} does not set an immediate")


def _moz_u32(exe: bytes, layout: dict, va: int) -> int:
    return struct.unpack_from("<I", exe, pe.file_offset(layout, va))[0]


def _moz_u8(exe: bytes, layout: dict, va: int) -> int:
    return exe[pe.file_offset(layout, va)]


# Tables of message ids, indexed by a game identity.
# - The Gallery's officer pages (MOZ 0x5577A0, WO2 X360 0x822A9E20, WO1 X360 0x8226BE40): the officer picks a series
#   group with the same comparisons (MOZ 0x5578E6-0x5579E8, WO2 0x822A9F50-0x822AA050; officers 0-90 get groups 0-10
#   in both, MOZ's 91-92 group 11), and the slot is page * 4 + cursor (MOZ 0x557A0D, WO2 0x822AA070). The name of
#   (group, slot) is message names[group][slot]: MOZ 8 slots a group, the donors 6. WO1's group code (0x8226BF30)
#   gives the same officers MOZ's groups 0-4 as its groups 0-4, MOZ's 6 as its 5 and MOZ's 8 as its 7; it puts
#   officers 0x34, 0x38 and 0x40 in one group, which WO2 and MOZ split into groups 5 and 10 with other series. WO1's
#   slots 4 and 5 show a series movie still, where WO2 (and MOZ after it) show the previous titles' movies.
GALLERY_NAMES = {"moz": (0x713828, 8), "wo2-x360": (0x82046128, 6), "wo1-x360": (0x82471F50, 6)}
GALLERY_GROUPS = {"wo2-x360": tuple((g, g) for g in range(11)),
                  "wo1-x360": ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (6, 5), (8, 7))}
GALLERY_SLOTS = {"wo2-x360": 6, "wo1-x360": 4}
# - An officer past the group tables shows special entry k: names MOZ 0x7139A8 / WO2 0x8204611C, unlock conditions
#   MOZ 0x71381C / WO2 0x82046110, three each, read with the same index (MOZ 0x557A7E, 0x557B15; WO2 0x822AA0E0).
GALLERY_SPECIAL = ((Table(0x7139A8, 3), Table(0x8204611C, 3)), (Table(0x71381C, 3), Table(0x82046110, 3)))
# - Slot s's unlock condition, MOZ 0x7137FC[s] / WO2 0x820460F8[s]. Slot 0 tests an officer flag and slot 1 all four
#   abilities in both; the others test proficiency / 1000 against N (MOZ 0x4756D0, jump table 0x475940: 24, 29, 34,
#   39, 44, 49 for slots 2-7; WO2 0x821979B8, table 0x821979F4: 19, 29, 39, 49 for slots 2-5). Slots join on N.
GALLERY_CONDITIONS = (0x7137FC, 0x820460F8, ((0, 0), (1, 1), (3, 3), (5, 4), (7, 5)))
# - The movie list: entry i is titled base + i and plays movie ids[i] (MOZ 0x556590: 36 entries, ids 0x713688, title
#   lea at 0x556809, play 0x514520; WO2 0x822A8EF0: 28, ids 0x82046088, title addi 0x822A91C4; WO1 0x8226AFE0: 7, ids
#   0x82039E98, title addi 0x8226B1D0). MOZ opens id m as movie%03d (m < 36), movie1%02d (m - 36, to 45) or
#   movie2%02d (m - 46) (0x5D3CBF); the donors open MEV%02u of the id (WO2 0x82193510, WO1 0x8217EE0C). Entries join
#   on the movie file data/movies.json gives the MOZ movie.
MOVIES = {"moz": (Table(0x713688, 36), 0x556809), "wo2-x360": (Table(0x82046088, 28), 0x822A91C4),
          "wo1-x360": (Table(0x82039E98, 7), 0x8226B1D0)}
# - Mission names: list position p is titled base + p (MOZ 0x5530E7 in 0x552F60, WO2 0x822A83D8 in 0x822A8358), and
#   the list holds one stage id per position (MOZ 30 bytes at 0x6D5A53, found by 0x47B370; WO2 30 at 0x820086C0, by
#   0x8219A748). Positions join on the stage: MOZ stage s is WO2 X360 stage s for s < 68 (both load stage s's script
#   by id, MOZ 0x4F06D0, WO2 0x821B9BF0; script_alignment pairs them by their voices and DREAM_SCRIPTS).
MISSIONS = {"moz": (0x6D5A53, 0x5530E7), "wo2-x360": (0x820086C0, 0x822A83D8)}
MISSION_COUNT = 30
SHARED_STAGES = 68


def _movie_files(movies: list[dict]) -> dict[str, tuple[str, int]]:
    """MOZ movie slot to (donor, donor movie id) of its donor file."""
    files = {}
    for row in movies:
        if match := re.fullmatch(r"mev(\d+)\.wmv", PurePosixPath(row["source"]).name, re.IGNORECASE):
            files[row["slot"]] = (row["donor"], int(match[1]))
    return files


def _moz_movie(m: int) -> str:
    return f"movie{m:03d}" if m < 36 else f"movie1{m - 36:02d}" if m < 46 else f"movie2{m - 46:02d}"


def _id_tables(exe: bytes, layout: dict, program: Program, movies: list[dict]) -> list[tuple[int, int]]:
    """(stock message id, donor message id) from the tables of message ids."""
    donor, pairs = program.donor, []
    if donor in GALLERY_NAMES:
        (ours, our_slots), (theirs, their_slots) = GALLERY_NAMES["moz"], GALLERY_NAMES[donor]
        pairs += [(_moz_u32(exe, layout, ours + 4 * (our_slots * g + s)), program.word(theirs + 4 * (their_slots * h + s)))
                  for g, h in GALLERY_GROUPS[donor] for s in range(GALLERY_SLOTS[donor])]
    if donor == "wo2-x360":
        for ours, theirs in GALLERY_SPECIAL:
            pairs += [(_moz_u32(exe, layout, ours.va + 4 * k), program.word(theirs.va + 4 * k)) for k in range(ours.count)]
        ours_va, theirs_va, slots = GALLERY_CONDITIONS
        pairs += [(_moz_u32(exe, layout, ours_va + 4 * s), program.word(theirs_va + 4 * t)) for s, t in slots]
        (our_list, our_title), (their_list, their_title) = MISSIONS["moz"], MISSIONS[donor]
        our_base, their_base = _moz_immediate(exe, layout, our_title), program.immediate(their_title)
        positions = {program.data[their_list - program.base + p]: p for p in range(MISSION_COUNT)}
        for p in range(MISSION_COUNT):
            stage = _moz_u8(exe, layout, our_list + p)
            if stage < SHARED_STAGES and stage in positions:
                pairs.append((our_base + p, their_base + positions[stage]))
    if donor in MOVIES:
        files = _movie_files(movies)
        (our_ids, our_title), (their_ids, their_title) = MOVIES["moz"], MOVIES[donor]
        our_base, their_base = _moz_immediate(exe, layout, our_title), program.immediate(their_title)
        entry = {program.word(their_ids.va + 4 * j): j for j in range(their_ids.count)}
        for i in range(our_ids.count):
            source, movie = files.get(_moz_movie(_moz_u32(exe, layout, our_ids.va + 4 * i)), (None, None))
            if source == donor and movie in entry:
                pairs.append((our_base + i, their_base + entry[movie]))
    return pairs


def message_join(exe: bytes, program: Program, movies: list[dict]) -> dict[int, set[int]]:
    """Stock message id to the donor message ids its twin code reads."""
    layout = pe.layout(exe)
    pairs = []
    for anchor in ANCHORS[program.donor]:
        stock = _moz_immediate(exe, layout, anchor.moz)
        theirs = program.immediate(anchor.donor) * anchor.donor_scale
        pairs += [(stock + anchor.step * k, theirs + anchor.step * k)
                  for k in range(anchor.first, anchor.first + anchor.count)]
    pairs += _id_tables(exe, layout, program, movies)
    joined: dict[int, set[int]] = {}
    for stock, theirs in pairs:
        if 0 <= stock < MESSAGES["moz"].count and 0 <= theirs < MESSAGES[program.donor].count:
            joined.setdefault(stock, set()).add(theirs)
    return joined


# Tables of (name, reading) pairs: MOZ names 0x6F6368 (read at 0x49E850, readings at 0x58C9F0), weapons 0x6FA5F8
# (0x52CF73, readings 0x595352); WO2 X360 names 0x8202B190 (0x822A29EC), weapons 0x8202CD98 (0x822ADC94); WO1 X360
# names 0x82022718 (0x822657CC), weapons 0x82024178 (0x82250304).
READING_TABLES = {
    "names": {"moz": Table(0x6F6368, 890, 8), "wo2-x360": Table(0x8202B190, 888, 8),
              "wo1-x360": Table(0x82022718, 835, 8)},
    "weapons": {"moz": Table(0x6FA5F8, 384, 8), "wo2-x360": Table(0x8202CD98, 412, 8),
                "wo1-x360": Table(0x82024178, 359, 8)},
}

# Tables both games index with the same id. MOZ reader, then each donor's. The count joined is the shorter table.
SAME_ID_TABLES = {
    # A name part and a weapon on the PSP (names 0x243210, read at 0x7622C; weapons 0x244E28, 0x7C40C `lhu; sll 3`).
    "names": {"moz": Table(0x6F6368, 890, 8), "wo2-psp": Table(0x243210, 890, 8)},
    "weapons": {"moz": Table(0x6FA5F8, 384, 8), "wo2-psp": Table(0x244E28, 384, 8)},
    # An ability's effect, by ability (MOZ 0x593FF0; WO2 0x822D7D84; WO1 0x8228C77C).
    "abilities": {"moz": Table(0x6F8100, 18), "wo2-x360": Table(0x8202CD50, 18), "wo1-x360": Table(0x82024130, 18)},
    # A command's name on the controls page, by command (MOZ 0x594010; WO2 0x822DA8A8; WO1 0x8228E23C).
    "commands": {"moz": Table(0x6FD0B8, 23), "wo2-x360": Table(0x8202DF00, 23), "wo1-x360": Table(0x82025098, 23)},
    # A skill's name, by skill (MOZ 0x5269F0; WO2 0x8228240C).
    "skills": {"moz": Table(0x6FD310, 15), "wo2-x360": Table(0x8202DEC4, 15)},
    # A result label, by label (MOZ 0x594020; WO2 0x822D7E6C).
    "result labels": {"moz": Table(0x6FD3A8, 11), "wo2-x360": Table(0x8202DF5C, 11)},
    # A treasure's name, by treasure (MOZ 0x552E20; WO2 0x822A80E8).
    "treasures": {"moz": Table(0x6FD220, 30), "wo2-x360": Table(0x8202DF88, 30)},
    # An army's name, by the army id a stage gives a force (MOZ 0x49E860; WO2 0x821C03BC; PSP 0x49738, a force's army
    # byte). MOZ adds armies at the end; the PSP adds the same 36 (its first 56 are WO2's).
    "armies": {"moz": Table(0x6FB7C0, 92), "wo2-x360": Table(0x8202DA78, 56), "wo2-psp": Table(0x245B18, 92)},
    # A base's name, by base id (MOZ 0x4F9040; WO2 0x821C1844; PSP 0x3934C). MOZ adds bases at the end, the PSP 66 of
    # them (its first 84 are WO2's).
    "bases": {"moz": Table(0x6FCC98, 151), "wo2-x360": Table(0x8202DD50, 84), "wo2-psp": Table(0x24602C, 150)},
    # A music track's title, by track (MOZ 0x572160; WO1 0x82279EBC). MOZ adds WO2's tracks at the end.
    "music": {"moz": Table(0x6FBCE0, 70), "wo1-x360": Table(0x82024D98, 38)},
    # A battle's name, by battle (MOZ 0x552E10; WO2 0x822A83B0). MOZ adds battles at the end.
    "battles": {"moz": Table(0x6FC558, 137), "wo2-x360": Table(0x8202DC30, 68)},
}

# MOZ's added entries that are another donor's: (table, first stock index, donor, donor table from the entry joined).
OFFSET_TABLES = (
    # WO1 stage r is MOZ stage 72 + r, whose battle is 68 + r (MOZ 0x5877C0 / 0x553093: s - 4 for 68-128, 124 for
    # 129-131). WO1 names stage r by its record's byte 0, j (ENS 13; 0x8226DB28), as entry 110 + j of its stage-name
    # table 0x82024CB0 (0x82267090), j = r below 56 and 56 for the last four, as MOZ folds its last three onto 124.
    ("battles", 68, "wo1-x360", Table(0x82024CB0 + 4 * 110, 57)),
    # MOZ's Z Dream stages 104 + k, k 28-39, are battles 125 + j (j = k - 28; 0x5471F7, then 0x5877C0 - 7). The PSP
    # takes Dream stage 44 + k, battle 68 + j after its own fold (0x20FB44-0x20FB60), from (name, reading) pairs.
    ("battles", 125, "wo2-psp", Table(0x245D8C + 8 * 68, 12, 8)),
)


# Each game keeps one 38-byte record per stage after a u32 count, picked by the stage id that picks the stage script
# (MOZ LINKDATA1 entry 0xFEB, loaded at 0x4EDCC0, read at 0x4F0160; WO2 X360 ENS 10, 0x821B7120 / 0x821B9888; WO1 X360
# ENS 13, 0x8219D580). Byte 5 counts the forces and byte 6 + k is force k's army id (MOZ 0x4F0213, WO2 0x821B992C,
# WO1 0x8219D628); bytes 26-33 list the stage's bases, each below the program's none value (MOZ 0x53E500: 0x97, WO2
# 0x82297668: 0x54, WO1 0x8225CDE0: 0x43). Twin stages (script_alignment.stage_joins) join their armies force by
# force, where both have as many forces, and their bases slot by slot.
STAGE_RECORDS = {"moz": 0xFEB, "wo2-x360": 10, "wo1-x360": 13}
# A donor stage's script entry (WO1 0x8219D948: ENS 88 + r; WO2 0x821B9BF0: ENS 103 + s), as stage_joins names it.
SCRIPT_ENTRIES = {"wo1-x360": 88, "wo2-x360": 103}
STAGE_RECORD = 38
NO_BASE = {"moz": 0x97, "wo2-x360": 0x54, "wo1-x360": 0x43}


def stage_records(data: bytes, order: str) -> list[bytes]:
    count = struct.unpack_from(order + "I", data)[0]
    return [data[4 + STAGE_RECORD * i:4 + STAGE_RECORD * (i + 1)] for i in range(count)]


def stage_record_join(ours: list[bytes], theirs: list[bytes], twins: dict[int, int], donor: str
                      ) -> dict[str, set[tuple[int, int]]]:
    """(stock index, donor index) pairs of the armies and bases twin stages give the same forces and base slots."""
    pairs: dict[str, set[tuple[int, int]]] = {"armies": set(), "bases": set()}
    for stage, twin in twins.items():
        a, b = ours[stage], theirs[twin]
        if a[5] == b[5]:
            pairs["armies"] |= {(a[6 + k], b[6 + k]) for k in range(a[5])}
        pairs["bases"] |= {(x, y) for x, y in zip(a[26:34], b[26:34], strict=True)
                           if x < NO_BASE["moz"] and y < NO_BASE[donor]}
    return pairs


def stages(game: Path, resolver) -> dict[str, tuple[list[bytes], dict[int, int]]]:
    """program_join's stages: the stock records, and per Xbox 360 donor its records and each stock stage's twin."""
    from wozmod.formats.linkdata import archive
    from wozmod.text.script_alignment import moz_scripts, stage_joins, wo1_scripts, wo2_x360

    folder = resolver.donors.folder
    joins = stage_joins(moz_scripts(game / "LINKDATA2"), wo1_scripts(folder("wo1-x360") / "linkdata_ens"),
                        wo2_x360(folder("wo2-x360") / "linkdata_ens").scripts())
    found = {"moz": (stage_records(archive(game / "LINKDATA1").read([STAGE_RECORDS["moz"]])[STAGE_RECORDS["moz"]], "<"),
                     {})}
    for donor in X360:
        found[donor] = (stage_records(resolver.entry(donor, STAGE_RECORDS[donor]), ">"),
                        {stage: join.entry - SCRIPT_ENTRIES[donor] for stage, join in joins.items()
                         if join.source == donor})
    return found


# The donors' army and base name tables, as stage records index them (WO1 0x821A3AB8 and 0x82259650, English branch).
STAGE_TABLES = {"armies": {"wo2-x360": Table(0x8202DA78, 56), "wo1-x360": Table(0x82024CB0, 58)},
                "bases": {"wo2-x360": Table(0x8202DD50, 84), "wo1-x360": Table(0x82024F50, 67)}}


def moz_entries(exe: bytes, table: Table, field: int = 0) -> list[bytes]:
    return pe.pointer_strings(exe, table.va + 4 * field, table.count, table.stride)


def _string(program: Program, va: int) -> bytes:
    at = va - program.base
    return program.data[at:program.data.index(b"\0", at)]


def program_join(exe: bytes, programs: dict[str, bytes], movies: list[dict],
                 stages: dict[str, tuple[list[bytes], dict[int, int]]] | None = None
                 ) -> dict[tuple[str, int], dict[str, list]]:
    """(stock table, id) to, per donor, the table.json pointers of the donor strings the join gives it.

    stages, when given, holds the stock stage records under "moz" and, per Xbox 360 donor, its stage records and
    each stock stage's twin stage."""
    joined: dict[tuple[str, int], dict[str, list]] = {}

    def add(table: str, index: int, program: Program, va: int) -> None:
        if (ref := program.ref(va)) is None:
            return
        found = joined.setdefault((table, index), {}).setdefault(program.donor, [])
        if ref not in found:
            found.append(ref)

    for donor in DONORS:
        program = Program(donor, programs[donor])
        strings = program.entries(MESSAGES[donor])
        for stock, ids in message_join(exe, program, movies).items():
            for theirs in sorted(ids):
                add("messages", stock, program, strings[theirs])
        for name, tables in READING_TABLES.items():
            if donor not in tables:
                continue
            by_reading: dict[bytes, list[int]] = {}
            for va, reading in zip(program.entries(tables[donor]), program.entries(tables[donor], 1), strict=True):
                by_reading.setdefault(_string(program, reading), []).append(va)
            for index, reading in enumerate(moz_entries(exe, tables["moz"], 1)):
                for va in by_reading.get(reading, []):
                    add(name, index, program, va)
        for name, tables in SAME_ID_TABLES.items():
            if donor in tables:
                for index, va in enumerate(program.entries(tables[donor])[:tables["moz"].count]):
                    add(name, index, program, va)
        for name, first, table_donor, table in OFFSET_TABLES:
            if table_donor == donor:
                for k, va in enumerate(program.entries(table)):
                    add(name, first + k, program, va)
        if stages and donor in stages:
            for name, pairs in stage_record_join(stages["moz"][0], *stages[donor], donor).items():
                names = program.entries(STAGE_TABLES[name][donor])
                for index, theirs in sorted(pairs):
                    if index < SAME_ID_TABLES[name]["moz"].count and theirs < len(names):
                        add(name, index, program, names[theirs])
    return joined


def stock_tables(exe: bytes) -> dict[str, list[bytes]]:
    """Every joined stock table's strings by id."""
    found = {"messages": moz_entries(exe, MESSAGES["moz"])}
    for name, tables in (*READING_TABLES.items(), *SAME_ID_TABLES.items()):
        found[name] = moz_entries(exe, tables["moz"])
    return found

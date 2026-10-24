"""Which donor stage title each MOZ stage title serves, joined through the donor programs' own stage tables.

Menu art takes WO2 X360 first. MOZ keeps WO2's stage ids below 68 (its LINKDATA1 4075 records 0-67 agree with WO2 X360
ENS 10's) and gives them title index = stage id (0x58EEA0: `cmp ebx, 0x44`). WO2 X360 reads the title of stage s from
ENS 11 at s * 0x8800 (0x822D2CD8, loader id 0xEF5), s being the stage id the scenario code keeps at [obj + 0x55E0]
(stored at 0x822D115C, passed at 0x822AE5D8). So MOZ title m < 68 is WO2 ENS 11 member m.

The titles from 68 on are WO1 stages:

WO1 X360 reads the title of stage j from ENS 14 at j * 0x8800 (0x822884A0: loader id 0xA61, ENS 14), j being byte 0
of the stage's 38-byte record in ENS 13 (stored at 0x8224C598 and 0x82286D1C, passed at 0x8226DB28 and 0x8226DEBC).
Its stage names are entries 110 on of the English message table 0x82024CB0 (picked by language at 0x822670D4): one
pointer per j, 57 of them, then a 0 word, as many as the distinct byte-0 values in ENS 13. A MOZ title whose name is
one of those strings takes that stage's title picture: member j for the name at entry 110 + j. (The table index being
j, not the record index, is inferred from the count: ENS 13 records 56-59 share j = 56, and the table stops at 57.)
"""

from __future__ import annotations

import struct

X360_BASE = 0x82000000
STAGE_NAMES = 0x82024CB0 + 4 * 110


def wo1_stage_names(program: bytes) -> list[int]:
    """The VAs of the WO1 X360 stage names, by stage title index j."""
    names, at = [], STAGE_NAMES - X360_BASE
    while pointer := struct.unpack_from(">I", program, at + 4 * len(names))[0]:
        names.append(pointer)
    return names


def title_members(names: list, program: bytes) -> list[int | None]:
    """For each stage name (a stored form with a "ref", or authored text), the WO1 ENS 14 member its title takes:
    the stage index j whose WO1 name it points at, or None for a name WO1 does not have."""
    stages = {va - X360_BASE: j for j, va in enumerate(wo1_stage_names(program))}
    return [stages.get(name["ref"][2]) if isinstance(name, dict) and name.get("ref", [None, None])[:2] == ["wo1-x360", "pe"]
            else None for name in names]


WO2_STAGES = 0x44  # MOZ titles below this are WO2 stage ids (0x58EF0B)


def title_sources(names: list, program: bytes) -> list[dict[str, int]]:
    """For each MOZ title index, the donor members its picture may come from, in menu-art donor order: WO2 X360 ENS 11
    member m for a WO2 stage, then the WO1 X360 ENS 14 member of its WO1 name (title_members). Empty: lettered."""
    return [({"wo2-x360": m} if m < WO2_STAGES else {}) | ({"wo1-x360": j} if j is not None else {})
            for m, j in enumerate(title_members(names, program))]

"""Which stock code each wozmod feature needs unchanged: the one guard list.

Other mods may change OROCHI_Z.exe. The DLL checks that the game is version 1.00 from its PE header, then compares
every guard with the running game before it patches anything. wozmod is all or nothing: when any guard differs, the
DLL patches nothing and logs each differing guard by its feature, and the installer stops before it installs (the
features only name what another mod changed). The build compiles this list into the DLL and exports it as
``wozmod_guards``; the installer reads the same bytes back from the DLL (guard_report).

A feature's guards: the prologue of each hook it installs and the stock bytes of each edit it writes (both come from
the build), the first CALL_BYTES bytes of each stock function its C calls (``calls``), the call before each return
address its C compares (``returns``: the 5 bytes before it), and the code bytes its C reads or rewrites itself
(``spans``). Bytes come from the stock executable at build time; only addresses live here. The window import slots
display sizes redirects have no guard: the DLL keeps what each slot held and calls through it, so another mod's hook
there keeps working, while a compatibility shim or overlay that rebinds a slot would wrongly turn wozmod off.
Addresses named
in runtime/english_runtime.c are read from it (``_c``), so a site added there is guarded here without a second copy;
tests/test_runtime_guards.py goes red on any .text address the C names that no guard covers.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from wozmod.formats import pe

from . import layout, true_rate
from .text_room import ROOMS, WIDE

V100 = (0x4AF69DF7, 0x101A000)  # version 1.00's TimeDateStamp (2009-11-08 10:31:19 UTC) and SizeOfImage
CALL_BYTES = 8  # of each stock function the runtime calls: enough for any jump another mod writes over its entry
MAGIC, VERSION = b"WOZGUARD", 3


@dataclass(frozen=True)
class Feature:
    key: str  # its C name (F_<KEY>)
    name: str  # in runtime.log: "refused: <name>, the code differs from stock at 0x..."
    requires: tuple[str, ...] = ()  # features it cannot work without: the installer names core by those that need it
    shown: str | None = None  # the installer's name for it, where the log's means nothing to a player
    hooks: tuple[int, ...] = ()  # hook ids (english_runtime.HOOKS and the other hook lists)
    edits: tuple[str, ...] = ()  # the edit lists it writes, by build.py's names
    calls: tuple[int, ...] = ()
    returns: tuple[int, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()  # (VA, length)
    define: str | None = None  # only in builds with this define


@dataclass(frozen=True)
class Guard:
    feature: int  # index into the build's features
    va: int
    stock: bytes


def _c(*names: str) -> tuple[int, ...]:
    """The values of these runtime #defines."""
    return tuple(layout.DEFINES[name] for name in names)


def _column(array: str, *columns: int) -> tuple[int, ...]:
    """These columns of a runtime C array, row by row (a one-dimensional array: every element)."""
    rows = layout.c_array(array)
    return tuple(v for row in rows for i, v in enumerate(row) if i in columns) if rows and isinstance(rows[0], tuple) \
        else tuple(rows)


def _hud_targets() -> tuple[int, ...]:
    return tuple(sorted({target for _, target, _ in layout.c_array("hud_calls")}))


TEXT_RETURNS = (
    _c("PROMPT_DRAW", "HISTORY_DRAW", "WINDOW_MEASURE", "NARRATION_DRAW", "STILL_DRAW", "STILL_PLATE", "STILL_Z_PLATE",
       "BRIEFING_DRAW", "NARRATION_Z_DRAW", "STILL_Z_DRAW", "NARRATION_Z_COUNT", "BRIEFING_COUNT", "SCENARIO_TEXT_DRAW",
       "DREAM_SUMMARY_DRAW", "ARMY_NAME_DRAW", "WEAPON_READING_DRAW", "WEAPON_INFO_READING_DRAW", "BANNER_TEXT_DRAW",
       "ARMY_TITLE_OUTLINE", "ARMY_TITLE", "BANNER_NAME_DRAW", "PLAIN_BOX_CALL", "DIALOG_PLATE_1", "DIALOG_PLATE_2",
       "SPEAK_PARSE")
    + (0x5990EE, 0x5995D9)  # STILL_Z_COUNTS
    + _column("glyph_sites", 0) + _column("boxed", 0) + _column("reflow", 0) + _column("described", 0)
    + _column("single_line", 0) + _column("key_first", 0, 1)
    + (0x52E78F, 0x52E7C3, 0x52E7EE, 0x52E8BF, 0x52E8E9, 0x52E910, 0x533497, 0x5973F5, 0x5974CC)  # row_columns
    + (0x54E76F, 0x54F8CF, 0x55194F)  # english_hook: message box, battle dialog, notice measures
    + tuple(r.call + 5 for r in ROOMS if r.room == WIDE) + tuple(r.read + 5 for r in ROOMS if r.read)
)
SHEET_TEXT = (0x5DDAB0, 0x5DCA30, 0x5DD700)  # sheet_text: a hi-res sheet's band as a quad, finalized and flushed

FEATURES = (
    Feature("core", "core", hooks=(24, 48)),  # the message pump (each frame) and the 4079 handles (once): shared
    Feature("served", "served entries", shown="English menu art and captions", hooks=(30, 31),
            calls=_c("ARCHIVE_WHOLE", "GAME_HEAP", "HEAP_ALLOC", "GAME_FREE")),
    Feature("text", "English text", requires=("core", "served"),
            hooks=(1, 2, 3, 4, 44, 5, 6, 7, 9, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 32, 19, 20, 26, 33, 34, 35, 42,
                   43, 108, 111, 66, 67, 68, 69),
            edits=("FONT_ROUTE", "CODE_EDITS", "camp plate", "preview unlocks"),
            calls=_c("GAME_DRAW", "GAME_MEASURE", "NAME_GLYPHS", "GAME_QUAD", "GAME_FONT", "CAST_SLOT",
                     "NARRATION_VOICE_START", "STILL_VOICE_START", "TAG_TEXTURE", "TEXTURE_HANDLE")
            + (0x54BBB0,)  # the caption line draw, through draw_fit_at
            + (0x5901C0, 0x593C40, 0x591810, 0x58C940),  # called from CODE_EDITS caves and fusion's cell (0x52C900)
            returns=TEXT_RETURNS),
    Feature("voices", "English voices", requires=("served",), hooks=(36, 37, 38, 39, 40, 41, 71, 109, 110, 118),
            edits=("VOICE_EDITS",), calls=_c("VOICE_PLAY", "READER_IDLE"),
            returns=(0x49E65F,  # the speak command's lip block (hook 71)
                     0x5D2AEA)),  # SE.bin's size asked (hook 31)
    Feature("rows", "Game Settings rows", requires=("core",), hooks=(27, 28, 29), edits=("settings rows",),
            calls=_c("GAME_SOUND") + (0x475B20, 0x475B30) + SHEET_TEXT),  # the switches' getter and setter
    Feature("fov", "field of view", hooks=(45, 46)),
    Feature("pads", "controllers", requires=("core",), hooks=(47, 70, 113, 106, 120, 121, 122, 123, 49, 50, 51, 52, 65),
            edits=("PAD_CAMERA", "SAVED_PAD", "STANDARD_CAMERA"),
            calls=_c("PAD_AUTO", "PACKAGE_ITEM", "TEXTURE_HANDLE", "GAME_NEW", "GAME_DELETE", "DINPUT_CREATE", "PAD_ADD",
                     "PAD_INIT", "PAD_NUMBER", "GAME_QUAD")
            + (0x5E4570,),  # the pad enumeration callback handed to EnumDevices
            spans=((layout.DEFINES["START_WIDTH"] - 1, 2), (layout.DEFINES["STICK_WIDTH"] - 1, 2),  # push 0x1E
                   (layout.DEFINES["WIDE_LAST_DRAW"] - 2, 3), (layout.DEFINES["WIDE_LAST_MEASURE"] - 2, 3))),  # cmp eax, 0x14
    Feature("fps", "frame rate", requires=("core",), hooks=(114, 115, 116, 117),
            calls=_c("DISPLAY_APPLY_AT"),  # a FRAME RATE or VSYNC change applies the display at once
            spans=((layout.DEFINES["FPS_LIMITER"], 2),)  # push 60
            + tuple((site.va, len(site.stock)) for site in true_rate.load())),  # the catalogue rewrites each
    Feature("art", "sharp menu art", requires=("core",), hooks=(21, 22),
            calls=_c("TEXTURE_HANDLE", "GAME_QUAD") + (0x5DE620,),
            returns=(0x53326A,)),  # the camp help plate (entry 4060)
    Feature("display", "display sizes",
            hooks=(75, 76, 77, 78, 80, 81, 101, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 98, 102, 103, 104, 94,
                   95, 96, 79, 97, 107),
            calls=_c("FTOL", "GAME_SOUND") + (0x5CA090, 0x5CAAD0, 0x56D270, 0x5DC070) + _hud_targets() + SHEET_TEXT,
            returns=(0x5CB464, 0x5CB46C, 0x5CB64C, 0x5CB8D2, 0x5CB8DA,  # layout_store
                     0x47C97A, 0x47C981, 0x5CB8E2, 0x5CBB32, 0x5CF3A4, 0x5CF4F3, 0x5D3F87, 0x5E9399,  # display_hook
                     0x56CC09, 0x56CC65),  # the Graphics ON and OFF plates (hook 21)
            spans=(tuple((va, 4) for va in _column("to_window", 0) + _column("to_scene", 0))
                   + tuple((va, 5) for va in _column("ftol_x") + _column("ftol_y") + _column("hud_calls", 0))
                   + ((0x56D19D, 3), (0x56D1CA, 3), (0x5CB432, 5), (0x56C803, 4), (0x56CB70, 4)))),
    Feature("movies", "English movies", hooks=(105,)),
    Feature("v101", "the version 1.01 fixes", hooks=(119,), edits=("V101_FIXES",),
            calls=(0x670730, 0x5D5D80, 0x5D5DC0,  # se_cutoff.h's query, stop and free
                   0x58E370,  # runtime_edits.DRAW_STATE, from the V101_FIXES caves
                   0x5DD620)),  # the render-state call the 0x582F24 detour moves into its cave
    # Builds with a define only; keep them last, so the features above keep their numbers in every build.
    Feature("distance", "render distance", spans=((layout.DEFINES["VIEW_GETTER"], 37),  # the two getters it rewrites
                                                  (layout.DEFINES["CROWD_GETTER"], 31)),
            define="ENRT_RENDER_DISTANCE"),
    Feature("moving", "moving camera fix", hooks=(72,), define="ENRT_MOVING_FIX"),
    Feature("test", "test harness", hooks=(73, 74, 112), define="ENRT_TEST",
            calls=_c("WINDOW_SET_TEXT", "WINDOW_OPEN", "WINDOW_STYLE", "QUEUE_SCRIPT", "GAIN_EXP", "GIVE_WEAPON",
                     "PLAY_SCENE", "SCENE_ARG", "SPAWN_SQUAD", "DESPAWN_SQUAD", "PLACE_UNIT", "CALL_HORSE", "MOUNT",
                     "CELL_FLAG")),
)


class GuardError(ValueError):
    """A hook or edit list no feature owns, a return address after no call, or an address with no stock bytes."""


def features(defines: tuple[str, ...] = ()) -> list[Feature]:
    """The features a build with ``defines`` has, in their C order."""
    return [f for f in FEATURES if f.define is None or f.define in defines]


def _after_call(exe: bytes, where: dict, va: int) -> bool:
    """Whether the instruction that ends at va is a call: e8 rel32, ff 15 [abs], or ff /2 through a register."""
    at = pe.file_offset(where, va)
    before = exe[at - 7:at]
    return (before[-5] == 0xE8 or before[-6:-4] == b"\xff\x15"
            or any(before[-n] == 0xFF and before[-n + 1] >> 3 & 7 == 2 and before[-n + 1] >> 6 == mode
                   for n, mode in ((2, 3), (2, 0), (3, 1), (6, 2))))


def _plan(edits: list[tuple[int, str, list]], hooks: list, defines: tuple[str, ...], chosen: list[Feature] | None
          ) -> tuple[list[Feature], list[tuple]]:
    """The build's features, and each guard as (feature, VA, size, stock bytes or None to read, whether it guards a
    return address)."""
    from .english_runtime import WIDTH_POINTER
    chosen = features(defines) if chosen is None else chosen
    index = {f.key: i for i, f in enumerate(chosen)}
    hook_owner = {h: i for i, f in enumerate(chosen) for h in f.hooks}
    edit_owner = {name: i for i, f in enumerate(chosen) for name in f.edits}
    plan = []
    for va, prologue, hook_id, *_ in hooks:
        if hook_id not in hook_owner:
            raise GuardError(f"no feature owns hook {hook_id}")
        plan.append((hook_owner[hook_id], va, len(prologue), prologue, False))
    for _, name, group_edits in edits:
        if name not in edit_owner:
            raise GuardError(f"no feature owns the edit list {name}")
        plan += [(edit_owner[name], va, len(before), before, False) for va, before, _ in group_edits]
    plan.append((index["text"], WIDTH_POINTER, 4, None, False))
    for i, f in enumerate(chosen):
        plan += [(i, va, CALL_BYTES, None, False) for va in f.calls]
        plan += [(i, va - 5, 5, None, True) for va in f.returns]
        plan += [(i, va, size, None, False) for va, size in f.spans]
    return chosen, plan


def guard_spans(edits: list[tuple[int, str, list]], hooks: list, defines: tuple[str, ...] = ()
                ) -> list[tuple[str, int, int]]:
    """(feature key, VA, size) of every guard a build has, without the stock executable."""
    chosen, plan = _plan(edits, hooks, defines, None)
    return [(chosen[feature].key, va, size) for feature, va, size, *_ in plan]


def guard_list(exe: bytes, edits: list[tuple[int, str, list]], hooks: list, defines: tuple[str, ...] = (),
               chosen: list[Feature] | None = None) -> tuple[list[Feature], list[Guard]]:
    """The build's features and every guard they have, read from the stock executable: each hook prologue and edit by
    its owner, the width pointer, then each feature's calls, returns and spans. ``edits`` are (group, list
    name, edits) as the build installs them, ``hooks`` its hook_sites(defines); ``chosen`` replaces the features."""
    chosen, plan = _plan(edits, hooks, defines, chosen)
    where = pe.layout(exe)

    def stock(va: int, size: int) -> bytes:
        try:
            first, last = pe.file_offset(where, va), pe.file_offset(where, va + size - 1)
        except pe.PEError as error:
            raise GuardError(f"guard at {va:#x} has no stock bytes") from error
        if last != first + size - 1:
            raise GuardError(f"guard at {va:#x} crosses a section")
        return exe[first:first + size]

    found = []
    for feature, va, size, known, returned in plan:
        if returned and not _after_call(exe, where, va + 5):
            raise GuardError(f"{chosen[feature].key}: {va + 5:#x} is not a return address in the stock executable")
        found.append(Guard(feature, va, stock(va, size) if known is None else known))
    return chosen, sorted(dict.fromkeys(found), key=lambda g: (g.feature, g.va, len(g.stock)))


def blob(chosen: list[Feature], guards: list[Guard], build: tuple[int, int] = V100) -> bytes:
    """The bytes the DLL exports as wozmod_guards: magic, version, feature count, the build stamp and image size, the
    guard count; per feature its name; per guard its feature, VA, length and stock bytes."""
    if len(chosen) > 32:
        raise GuardError("more than 32 features")
    out = bytearray(MAGIC + struct.pack("<HHIII", VERSION, len(chosen), *build, len(guards)))
    for f in chosen:
        name = f.name.encode("ascii")
        out += struct.pack("<B", len(name)) + name
    for g in guards:
        out += struct.pack("<BIH", g.feature, g.va, len(g.stock)) + g.stock
    return bytes(out)

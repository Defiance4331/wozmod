"""Fixed stock-byte checks and x86 runtime edits."""

from __future__ import annotations

import struct

from .latin_font import (
    LATIN_COUNT, LATIN_FIRST, LATIN_GLYPH, SURROGATE_CODE, SURROGATE_GLYPH, SURROGATE_LETTERS,
)

CAVE = 0x5F5100
PAD_PROMPTS = 0x1413FEC

ABILITY_CELL_GROWTH = 40
ABILITY_NAME_GROWTH = 40

def p32(value: int) -> bytes:
    """Pack an unsigned x86 immediate."""
    return struct.pack("<I", value)

def rel32(opcode: bytes, at: int, target: int) -> bytes:
    """Encode a five-byte relative branch."""
    return opcode + struct.pack("<i", target - at - 5)

def relocated(at: int, back: int, code: bytes, call: int | None = None) -> bytes:
    """Append an optional call and a return branch."""
    if call is not None:
        code += rel32(b"\xe8", at + len(code), call)
    return code + rel32(b"\xe9", at + len(code), back)

def assembled(at: int, *parts: bytes | int) -> bytes:
    """Append byte sequences and calls to integer addresses."""
    code = b""
    for part in parts:
        code += rel32(b"\xe8", at + len(code), part) if isinstance(part, int) else part
    return code

def detour(site: int, stock: bytes, cave: int, back: int, code: bytes, call: int | None = None) -> list:
    """Return checked edits for a branch and its relocated code."""
    moved = relocated(cave, back, code, call)
    return [(site, stock, rel32(b"\xe9", site, cave) + b"\x90" * (len(stock) - 5)),
            (cave, b"\xcc" * len(moved), moved)]

def widths(*sites: int, stock: int, wide: int) -> list:
    """Return checked push-immediate width edits."""
    return [(va, b"\x68" + struct.pack("<I", stock), b"\x68" + struct.pack("<I", wide)) for va in sites]

def scale_ends(site: int, stock: str, test: str, cave: int, top: int, leave: int | None = None) -> list:
    """An Options scale's step does nothing at its ends instead of wrapping: Left (``test`` nonzero: stored +1) at
    the lowest, stored 2; Right at the highest, stored ``top``. At ``site`` the step tests its direction (``test``,
    the head of ``stock``) with the stored value in eax; the step leaves through ``leave``, else pop esi; ret."""
    head, rest = bytes.fromhex(test), bytes.fromhex(stock)[len(bytes.fromhex(test)):]
    resume = len(head) + 14
    stop = resume + len(head) + len(rest) + 5
    code = (head + b"\x75\x07" + bytes([0x83, 0xF8, top, 0x74, stop - len(head) - 7, 0xEB, 0x05,
                                         0x83, 0xF8, 0x02, 0x74, stop - resume]) + head + rest
            + rel32(b"\xe9", cave + stop - 5, site + len(head) + len(rest)))
    code += b"\x5e\xc3" if leave is None else rel32(b"\xe9", cave + stop, leave)
    return [(site, head + rest, rel32(b"\xe9", site, cave) + b"\x90" * (len(head) + len(rest) - 5)),
            (cave, b"\xcc" * len(code), code)]

CODE_EDITS = [
    (0x5695C5, bytes.fromhex("8b0783f802"), bytes.fromhex("e98b000000")),

    # Closing the window (Alt+F4, the title bar's X) quits: the WM_CLOSE handler (0x5CBE40) asked with a Japanese
    # MessageBoxA(window, string 0x67, MB_YESNO) that full-screen and borderless windows hide; it now takes the box's
    # Yes. The call becomes add esp, 0x10 (its four arguments) and push 6; pop eax (IDYES), then the game's own path.
    (0x5CBE88, bytes.fromhex("ff1590c26c00"), bytes.fromhex("83c4106a0658")),

    (0x560A5E, bytes.fromhex("c70554f2c20004000000"), bytes.fromhex("c70554f2c20002000000")),

    *widths(0x55453E, 0x554549, 0x531931, 0x53193C, 0x53C8DB, 0x53C8E6,
            0x5845E8, 0x5845F3, 0x591C6C, 0x591C81, 0x5988DA, 0x5988E5,
            stock=336, wide=340),

    (0x525F85, bytes.fromhex("68c4000000"), bytes.fromhex("6800010000")),
    (0x525F8F, bytes.fromhex("68c4000000"), bytes.fromhex("6800010000")),

    *widths(0x533DD1, 0x533DDF, stock=196, wide=288),

    *widths(0x533ABE, 0x533AC8, stock=322, wide=556),

    (0x595ADE, bytes.fromhex("8d5723"), bytes.fromhex("8d5716")),
    (0x595AEF, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x595AF2, bytes.fromhex("8d4749"), bytes.fromhex("8d473c")),
    (0x595B0F, bytes.fromhex("8d979d000000"), bytes.fromhex("8d97aa000000")),
    *[(va, bytes.fromhex("6a46"), bytes.fromhex("6a60"))
      for va in (0x527821, 0x527829, 0x529655, 0x52965D, 0x52D819, 0x52D821, 0x56A848, 0x56A850)],
    (0x527832, bytes.fromhex("83c349"), bytes.fromhex("83c33c")),
    (0x529666, bytes.fromhex("83c349"), bytes.fromhex("83c33c")),
    (0x52D82A, bytes.fromhex("83c649"), bytes.fromhex("83c63c")),
    (0x56A859, bytes.fromhex("83c749"), bytes.fromhex("83c73c")),

    (0x52B5B7, bytes.fromhex("8d4b18"), bytes.fromhex("8d4b0b")),
    (0x52B5DA, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x52B5E1, bytes.fromhex("8d433f"), bytes.fromhex("8d4332")),
    (0x52B5F2, bytes.fromhex("8d8b9d000000"), bytes.fromhex("8d8baa000000")),
    (0x52BB4A, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x52BB52, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x52BB5B, bytes.fromhex("83c73f"), bytes.fromhex("83c732")),

    (0x533315, bytes.fromhex("8d4e15"), bytes.fromhex("8d4e0b")),
    (0x53332F, bytes.fromhex("8d86a1000000"), bytes.fromhex("8d86ac000000")),
    (0x533346, bytes.fromhex("6a5b"), bytes.fromhex("6a70")),
    (0x533349, bytes.fromhex("8d4e38"), bytes.fromhex("8d4e2e")),
    (0x533365, bytes.fromhex("6a46"), bytes.fromhex("6a70")),
    (0x53336D, bytes.fromhex("6a46"), bytes.fromhex("6a70")),
    (0x533372, bytes.fromhex("8d5642"), bytes.fromhex("8d562e")),

    *widths(0x5955BB, 0x52D077, 0x52D091, 0x53375B, 0x53376D, 0x59858F, 0x598E01, stock=0x1D8, wide=650),

    (0x598DD3, bytes.fromhex("69f6bb020000"), bytes.fromhex("69f6bd020000")),
    (0x598DE7, bytes.fromhex("8db40228feffff"), bytes.fromhex("8db40276fdffff")),
    (0x598DF0, bytes.fromhex("bee3000000"), bytes.fromhex("be33000000")),

    *detour(0x52CC8C, bytes.fromhex("6a1c 6a62 056c010000"), CAVE + 0x740, 0x52CC95,
            bytes.fromhex("6a1c 6896000000 056c010000")),

    *detour(0x595612, bytes.fromhex("6880000000 6a7c 68d8010000"), CAVE + 0x540, 0x59561E,
            bytes.fromhex("6880000000 6896000000 68d8010000")),
    *detour(0x59569E, bytes.fromhex("6880000000 6a7c 68d8010000"), CAVE + 0x560, 0x5956AA,
            bytes.fromhex("6880000000 6896000000 68d8010000")),

    *widths(0x52BBD1, stock=364, wide=400), *widths(0x52BBD8, stock=884, wide=848),
    *widths(0x52BBE1, stock=532, wide=548), *widths(0x52BBE8, stock=716, wide=700),
    *widths(0x529548, 0x52D736, stock=476, wide=544),
    *widths(0x52954F, 0x52D73D, stock=741, wide=673),
    *widths(0x5273F9, stock=392, wide=528), *widths(0x527400, stock=825, wide=689),
    *widths(0x527A2B, stock=392, wide=632), *widths(0x527A32, stock=825, wide=585),
    *widths(0x528EE5, stock=434, wide=648), *widths(0x528EEC, stock=786, wide=572),

    *widths(0x526BF8, 0x526C03, 0x526CB8, 0x526CC3, stock=196, wide=228),
    *widths(0x5274F7, 0x527500, 0x527C2D, 0x527C38, stock=168, wide=196),
    *widths(0x527E0C, 0x527E17, stock=168, wide=216),
    *widths(0x52704A, 0x527055, stock=196, wide=244),

    (0x52E5F6, bytes.fromhex("8d4e5b"), bytes.fromhex("8d4e31")),
    (0x52E60D, bytes.fromhex("8d860a010000"), bytes.fromhex("8d8634010000")),
    *detour(0x52E621, bytes.fromhex("6880000000 6a23 6a7e 57 83c507 8d467e 55 50 e8881b0600"), CAVE, 0x52E638,
            bytes.fromhex("6880000000 6a23 68d2000000 57 83c507 8d4654 55 50"), call=0x5901C0),
    *detour(0x52E642, bytes.fromhex("6a04 6880000000 6a23 6a7e 6a00 6a00 6a23 6a7e 6a00 8d467e 55 50"),
            CAVE + 0x40, 0x52E65C,
            bytes.fromhex("6a04 6880000000 6a23 68d2000000 6a00 6a00 6a23 68d2000000 6a00 8d4654 55 50")),

    *widths(0x526F2C, stock=630, wide=978),
    (0x526F63, bytes.fromhex("69d2cb000000"), bytes.fromhex("69d23f010000")),
    *widths(0x526F6B, 0x526F86, 0x526FD9, 0x526FE4, stock=196, wide=312),
    (0x527F10, bytes.fromhex("b845010000"), bytes.fromhex("b897000000")),
    (0x527F27, bytes.fromhex("69c9bb030000"), bytes.fromhex("69c969040000")),

    *widths(0x526B88, stock=441, wide=669),
    (0x526D03, bytes.fromhex("69d2c7000000"), bytes.fromhex("69d23b010000")),
    *widths(0x526D1B, 0x526D2B, 0x526D6B, 0x526D76, stock=196, wide=312),
    (0x52745A, bytes.fromhex("bf7d020000"), bytes.fromhex("bf30020000")),
    (0x5274B1, bytes.fromhex("69f683020000"), bytes.fromhex("69f6d0020000")),

    (0x531914, bytes.fromhex("8d8402b0feffff"), bytes.fromhex("8d840230feffff")),
    (0x53191D, bytes.fromhex("b8d4000000"), bytes.fromhex("b854000000")),
    (0x531976, bytes.fromhex("8d8c02b0feffff"), bytes.fromhex("8d8c0230feffff")),
    (0x531983, bytes.fromhex("c7442410d4000000"), bytes.fromhex("c744241054000000")),

    (0x53C8C1, bytes.fromhex("8d8402b0feffff"), bytes.fromhex("8d840230feffff")),
    (0x53C8CA, bytes.fromhex("b8e3000000"), bytes.fromhex("b863000000")),
    (0x53C920, bytes.fromhex("8dbc02b0feffff"), bytes.fromhex("8dbc0230feffff")),
    (0x53C92D, bytes.fromhex("c744240ce3000000"), bytes.fromhex("c744240c63000000")),

    (0x5296DC, bytes.fromhex("69ff9c010000"), bytes.fromhex("69ff1c020000")),
    (0x5296F0, bytes.fromhex("8d940ae3000000"), bytes.fromhex("8d940a63000000")),
    (0x529868, bytes.fromhex("69d29e010000"), bytes.fromhex("69d21e020000")),
    (0x5298AF, bytes.fromhex("8d940292feffff"), bytes.fromhex("8d940212feffff")),
    (0x5298EC, bytes.fromhex("c744240ce3000000"), bytes.fromhex("c744240c63000000")),
    (0x52D16E, bytes.fromhex("69d2a4010000"), bytes.fromhex("69d224020000")),
    (0x52D1B5, bytes.fromhex("8d940292feffff"), bytes.fromhex("8d940212feffff")),
    (0x52D1F2, bytes.fromhex("c7442410e3000000"), bytes.fromhex("c744241063000000")),

    (0x52ED89, bytes.fromhex("8d940292feffff"), bytes.fromhex("8d940212feffff")),
    (0x52ED90, bytes.fromhex("81fae3000000"), bytes.fromhex("81fa63000000")),
    (0x52ED9C, bytes.fromhex("c7442410e3000000"), bytes.fromhex("c744241063000000")),
    (0x527A84, bytes.fromhex("c7442410e3000000"), bytes.fromhex("c744241063000000")),
    (0x527A9A, bytes.fromhex("c7442410e3000000"), bytes.fromhex("c744241063000000")),

    (0x554521, bytes.fromhex("8d940ab0feffff"), bytes.fromhex("8d940a30feffff")),
    (0x55452A, bytes.fromhex("bae3000000"), bytes.fromhex("ba63000000")),
    (0x55460A, bytes.fromhex("8d8c0288feffff"), bytes.fromhex("8d8c0208feffff")),
    (0x55461E, bytes.fromhex("b9dc000000"), bytes.fromhex("b95c000000")),

    *widths(0x587C52, 0x587C5C, stock=308, wide=600),
    *widths(0x542B30, 0x542B50, 0x542B5A, stock=308, wide=600),

    (0x542B5F, bytes.fromhex("6a30"), bytes.fromhex("6a00")),

    *[(va, bytes.fromhex("6a3f"), bytes.fromhex("6a7f")) for va in (0x591DCC, 0x591DD2, 0x591E0B, 0x591E15)],
    (0x591E4D, bytes.fromhex("81c28d000000"), bytes.fromhex("81c2bc000000")),

    *widths(0x588BAD, 0x588BB8, 0x583620, 0x58362B, 0x568A7F, 0x568A8A, stock=252, wide=440),

    *detour(0x591EA2, bytes.fromhex("6a04 6880000000 6a20 6a54 6a00 6a00 b856555555 f7e9 6a20 6a54 57 53 8bea 56 c1ed1f"
                                    " 6897000000 03ea"), CAVE + 0x80, 0x591ECB,
            bytes.fromhex("6a04 6880000000 6a20 688c000000 6a00 6a00 b856555555 f7e9 6a20 688c000000 57 53 8bea 56"
                          " c1ed1f 6897000000 03ea")),
    (0x591EE8, bytes.fromhex("83c654eb038d4900"), bytes.fromhex("81c68c0000006690")),

    *[(va, bytes.fromhex("6a38"), bytes.fromhex("6a7c")) for va in (0x54FEEF, 0x54FEF7, 0x550139, 0x550141)],

    *widths(0x5389F7, 0x538A02, stock=294, wide=400),

    *widths(0x538A5E, 0x538A68, stock=322, wide=556),
    *widths(0x538A29, 0x538A34, stock=294, wide=436),

    *widths(0x538B0D, 0x538B18, stock=378, wide=512),

    *widths(0x53AB6C, 0x53AB77, 0x53ABAD, 0x53ABB8, stock=196, wide=312),

    *widths(0x53AB3E, 0x53AB45, stock=154, wide=196),


    *widths(0x5A5971, 0x5A597C, 0x5A6946, 0x5A6951, 0x5A69F6, 0x5A6A01, 0x5A6AC6, 0x5A6AD1, 0x5A6B69, 0x5A6B74,
            0x5A6D1A, 0x5A6D25, 0x5A6F2B, 0x5A6F36, 0x5A7122, 0x5A712D, stock=238, wide=412),
    *widths(0x5A67A4, 0x5A67AF, 0x5A6814, 0x5A681F, stock=140, wide=162),
    (0x5A67F8, bytes.fromhex("81c3a1000000"), bytes.fromhex("81c3b7000000")),

    *widths(0x5807AF, 0x5807BA, 0x58081D, 0x580828, stock=140, wide=162),
    (0x580804, bytes.fromhex("81c6a1000000"), bytes.fromhex("81c6b7000000")),

    *widths(0x5A5ADC, 0x5A5AE7, stock=168, wide=204),

    *widths(0x5A0BD7, 0x5A0BE2, stock=308, wide=532),

    *widths(0x580696, 0x5806A0, stock=196, wide=300),
    (0x58069C, bytes.fromhex("6a00"), bytes.fromhex("6a6a")),
    (0x5806A8, bytes.fromhex("83c30a"), bytes.fromhex("83c3d6")),

    *widths(0x580535, 0x580545, stock=168, wide=400),

    *((va, b"\x6a\x46", b"\x6a\x5c") for va in (0x57EBC5, 0x57EBCD, 0x568842, 0x56884A)),

    (0x5687C6, bytes.fromhex("8dbb03010000"), bytes.fromhex("8dbbef000000")),
    (0x568850, bytes.fromhex("81c31f010000"), bytes.fromhex("81c30b010000")),

    *widths(0x5808C5, 0x5808D0, stock=168, wide=184),

    (0x58F519, bytes.fromhex("683e010000"), bytes.fromhex("6803010000")),
    *widths(0x58F536, 0x58F544, stock=238, wide=384),
    *widths(0x58F531, 0x58F53F, stock=140, wide=160),
    (0x58F54B, bytes.fromhex("6822010000"), bytes.fromhex("680e010000")),
    (0x58F550, bytes.fromhex("68d4020000"), bytes.fromhex("6885020000")),

    *widths(0x551BA8, 0x551BB2, stock=308, wide=612),

    *widths(0x551D7A, 0x551D8D, stock=252, wide=312),
    (0x551D89, bytes.fromhex("6a00"), bytes.fromhex("6a64")),
    (0x551D95, bytes.fromhex("83c6e8"), bytes.fromhex("83c6ca")),

    (0x556D9F, bytes.fromhex("8d4e23"), bytes.fromhex("8d4e16")),
    (0x556DB4, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x556DB7, bytes.fromhex("8d4649"), bytes.fromhex("8d463c")),
    (0x556DD5, bytes.fromhex("8d869d000000"), bytes.fromhex("8d86aa000000")),

    (0x55B57B, bytes.fromhex("8d5723"), bytes.fromhex("8d5716")),
    (0x55B58C, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x55B58F, bytes.fromhex("8d4749"), bytes.fromhex("8d473c")),
    (0x55B5AC, bytes.fromhex("8d979d000000"), bytes.fromhex("8d97aa000000")),
    *[(va, bytes.fromhex("6a46"), bytes.fromhex("6a60")) for va in (0x5574DF, 0x5574E7, 0x55BB9A, 0x55BBA0)],
    (0x5574F0, bytes.fromhex("83c349"), bytes.fromhex("83c33c")),
    (0x55BBA8, bytes.fromhex("83c749"), bytes.fromhex("83c73c")),

    *widths(0x552F9D, 0x552FA8, 0x552FCF, 0x552FDA, stock=196, wide=312),

    *detour(0x553011, bytes.fromhex("6a1c 6a70 6a00 6a00 6a1c 6a70"), CAVE + 0x100, 0x55301D,
            bytes.fromhex("6a1c 68a0000000 6a00 6a00 6a1c 68a0000000")),
    *detour(0x553035, bytes.fromhex("6a1c 6a70 6a00 6a00 6a1c 6a70"), CAVE + 0x140, 0x553041,
            bytes.fromhex("6a1c 68a0000000 6a00 6a00 6a1c 68a0000000")),

    (0x55BCF7, bytes.fromhex("e8d4fafeff"), bytes.fromhex("0f1f440000")),

    (0x595368, bytes.fromhex("e86364fbff"), bytes.fromhex("0f1f440000")),

    (0x5519EE, bytes.fromhex("c70594d0c2009a010000"), bytes.fromhex("c70594d0c2006a010000")),
    (0x55128E, bytes.fromhex("81c59a010000"), bytes.fromhex("81c56a010000")),
    (0x551284, bytes.fromhex("83e581"), bytes.fromhex("83e5b1")),

    (0x54EC48, bytes.fromhex("83c208"), bytes.fromhex("83c209")),
    (0x54EF45, bytes.fromhex("83c208"), bytes.fromhex("83c209")),

    *widths(0x59C421, 0x59C42C, stock=266, wide=366),
    (0x59C48E, bytes.fromhex("b80a010000"), bytes.fromhex("b86e010000")),
    (0x59C3DF, bytes.fromhex("bfe8000000"), bytes.fromhex("bfb6000000")),
    (0x59C3E6, bytes.fromhex("bf13030000"), bytes.fromhex("bfe1020000")),

    *widths(0x537C0F, 0x537C1C, stock=140, wide=208),

    *widths(0x537BE8, 0x537BF3, stock=210, wide=232),

    (0x5959C4, bytes.fromhex("6a69"), bytes.fromhex("6a7f")),
    (0x5975E7, bytes.fromhex("6a69"), bytes.fromhex("6a7f")),
    *widths(0x598724, stock=154, wide=168),

    (0x5257E7, bytes.fromhex("8d8c0251ffffff"), bytes.fromhex("8d8c0229ffffff")),
    (0x5257F7, bytes.fromhex("c7442414f1000000"), bytes.fromhex("c7442414c9000000")),
    (0x5397EA, bytes.fromhex("8dbc0251ffffff"), bytes.fromhex("8dbc0229ffffff")),
    (0x5397F6, bytes.fromhex("bfff000000"), bytes.fromhex("bfd7000000")),

    (0x595876, bytes.fromhex("6a01"), bytes.fromhex("6a02")),
    (0x59589C, bytes.fromhex("6a01"), bytes.fromhex("6a02")),
    (0x595F26, bytes.fromhex("6a01"), bytes.fromhex("6a03")),
    (0x597550, bytes.fromhex("6a01"), bytes.fromhex("6a03")),
    *detour(0x595740, bytes.fromhex("b893000000"), CAVE + 0x7C0, 0x595745,
            bytes.fromhex("9c b893000000 837c242002 7505 83c0"
                          f"{ABILITY_CELL_GROWTH:02x} eb0a 837c242003 7503 83c0"
                          f"{ABILITY_NAME_GROWTH:02x} 9d")),
    *detour(0x595759, bytes.fromhex("8db7a1000000"), CAVE + 0x800, 0x59575F,
            bytes.fromhex("9c 8db7a1000000 837c242002 7505 83c6"
                          f"{ABILITY_CELL_GROWTH:02x} eb0a 837c242003 7503 83c6"
                          f"{ABILITY_NAME_GROWTH:02x} 9d")),
    *detour(0x59590B, bytes.fromhex("8d4f7e 8d442428"), CAVE + 0x840, 0x595912,
            b"\x8d\x8f" + p32(126 + ABILITY_CELL_GROWTH) + bytes.fromhex("8d442428")),
    *widths(0x59584E, 0x5959E4, 0x595A3A, 0x595A7E, 0x598826,
            stock=175, wide=175 + ABILITY_CELL_GROWTH),
    *widths(0x598747, stock=569, wide=569 + 3 * ABILITY_CELL_GROWTH),
    (0x598778, bytes.fromhex("69d2bd000000"), b"\x69\xd2" + p32(189 + ABILITY_CELL_GROWTH)),
    (0x598819, bytes.fromhex("69c9bd000000"), b"\x69\xc9" + p32(189 + ABILITY_CELL_GROWTH)),

    *detour(0x595528, bytes.fromhex("6bc969 8d4c1113"), CAVE + 0x6A0, 0x59552F,
            bytes.fromhex("69c99c000000 8d4c1113")),
    *detour(0x591844, bytes.fromhex("6a04 6880000000 50 6a62 56 b938000000"),
            CAVE + 0x6C0, 0x591854,
            bytes.fromhex("6a04 6880000000 50 6896000000 56 b938000000")),
    *detour(0x591856, bytes.fromhex("6a62 55 57 6689742440"), CAVE + 0x6E0, 0x59185F,
            bytes.fromhex("6896000000 55 57 6689742440")),
    (0x591A9A, bytes.fromhex("83c03b"), bytes.fromhex("83c06d")),
    (0x591ADC, bytes.fromhex("83c14d"), bytes.fromhex("83c17f")),
    *detour(0x52CCA5, bytes.fromhex("6bc969 81c187020000"), CAVE + 0x700, 0x52CCAE,
            bytes.fromhex("69c99c000000 81c187020000")),
    *detour(0x598652, bytes.fromhex("6bc969 8d441113"), CAVE + 0x720, 0x598659,
            bytes.fromhex("69c99c000000 8d441113")),
    *detour(0x598632, bytes.fromhex("6a1c 6a62 8bcb"), CAVE + 0x760, 0x598638,
            bytes.fromhex("6a1c 6896000000 8bcb")),

    *detour(0x52EC14, bytes.fromhex("6bd269 8b4c2468"), CAVE + 0x780, 0x52EC1B,
            bytes.fromhex("69d29c000000 8b4c2468")),
    *detour(0x52EC7A, bytes.fromhex("6a1c 6a62 6a00 53 57"), CAVE + 0x7A0, 0x52EC82,
            bytes.fromhex("6a1c 6896000000 6a00 53 57")),

    *widths(0x598CC5, 0x598CCF, stock=210, wide=332),

    (0x592323, bytes.fromhex("be69000000"), bytes.fromhex("be80000000")),
    (0x592335, bytes.fromhex("b98c000000"), bytes.fromhex("b994000000")),

    (0x5980BA, bytes.fromhex("6a46"), bytes.fromhex("6a68")),
    *detour(0x5980DB, bytes.fromhex("6a00 8d5718 52 8d4569 50 53 e855bbffff"), CAVE + 0xC0, 0x5980EB,
            bytes.fromhex("6a00 8d5718 52 8d858b000000 50 53"), call=0x593C40),

    (0x593C57, bytes.fromhex("6a23"), bytes.fromhex("6a28")),
    *detour(0x593C99, bytes.fromhex("6a46 51 8b4c243c 52 8b54243c 6a23 6a46 51 8b4c2444 52 51"), CAVE + 0x3C0, 0x593CB0,
            bytes.fromhex("688c000000 51 8b4c243c 52 8b54243c 6a28 688c000000 51 8b4c2444 52 51")),

    *widths(0x546B70, 0x546B7B, stock=308, wide=480),
    *widths(0x5475B5, 0x5475BF, stock=294, wide=440),
    (0x547623, bytes.fromhex("8d86e0010000"), bytes.fromhex("8d8690020000")),

    *detour(0x5475FB, bytes.fromhex("6a38 6a70 6a00 6a00 6a38 6a70"), CAVE + 0x180, 0x547607,
            bytes.fromhex("6a38 6896000000 6a00 6a00 6a38 6896000000")),
    (0x54760E, bytes.fromhex("8d4662"), bytes.fromhex("8d4630")),

    (0x547636, bytes.fromhex("81c6e2020000"), bytes.fromhex("81c6d4030000")),

    *[(va, bytes.fromhex("6a38"), bytes.fromhex("6a40")) for va in (0x5489FD, 0x548A06)],
    *widths(0x5489FF, 0x548A08, stock=294, wide=1024),
    *widths(0x548A13, stock=226, wide=134),

    *widths(0x56EAEA, 0x56EAF4, 0x56B595, 0x56B59F, 0x56FAC4, 0x570585, 0x57058F, 0x572432, 0x57243C,
            0x56BEE2, 0x56BEEC, 0x56CAC6, 0x56CAD0, 0x571BB2, stock=210, wide=310),
    *widths(0x56FAB9, 0x571BA8, stock=209, wide=309),
    *[(va, bytes.fromhex("6a00"), bytes.fromhex("6a65"))
      for va in (0x56EAF0, 0x56B59B, 0x56FAC0, 0x57058B, 0x572438, 0x56BEE8, 0x56CACC, 0x571BAE)],
    *detour(0x56BEF1, bytes.fromhex("6a00 56 53 6a02"), CAVE + 0x640, 0x56BEF7,
            bytes.fromhex("6a00 56 53 832c2436 6a02")),
    *detour(0x56CAD5, bytes.fromhex("6a00 56 57 6a02"), CAVE + 0x660, 0x56CADB,
            bytes.fromhex("6a00 56 57 832c2436 6a02")),
    *detour(0x571BB7, bytes.fromhex("6a00 53 56 6a02"), CAVE + 0x680, 0x571BBD,
            bytes.fromhex("6a00 53 56 832c2436 6a02")),
    *detour(0x56EAF9, bytes.fromhex("6a00 53 56 51 52"), CAVE + 0x400, 0x56EAFF,
            bytes.fromhex("6a00 53 56 832c2436 51 52")),
    *detour(0x56B5A4, bytes.fromhex("6a00 53 57 6a02"), CAVE + 0x420, 0x56B5AA,
            bytes.fromhex("6a00 53 57 832c2436 6a02")),
    *detour(0x56FAC9, bytes.fromhex("6a00 57 56 6a02"), CAVE + 0x440, 0x56FACF,
            bytes.fromhex("6a00 57 56 832c2436 6a02")),
    *detour(0x570594, bytes.fromhex("6a00 56 57 6a02"), CAVE + 0x460, 0x57059A,
            bytes.fromhex("6a00 56 57 832c2436 6a02")),
    *detour(0x572441, bytes.fromhex("6a00 56 53 6a02"), CAVE + 0x480, 0x572447,
            bytes.fromhex("6a00 56 53 832c2436 6a02")),

    *widths(0x56E884, 0x56E88E, stock=364, wide=412),

    *widths(0x57067E, 0x570688, 0x5706C3, 0x5706CD, stock=140, wide=200),
    (0x5705E7, bytes.fromhex("81c7e3000000"), bytes.fromhex("81c70b010000")),
    (0x5706D5, bytes.fromhex("81c78c000000"), bytes.fromhex("81c7c8000000")),
    *detour(0x570608, bytes.fromhex("896c241c c744242001000000"), CAVE + 0x4A0, 0x570614,  # its ON/OFF row: OFF left,
            bytes.fromhex("c744241c0f000000 c74424200e000000")),  # ON right (bands 15 and 14) ...
    (0x570614, bytes.fromhex("7454"), bytes.fromhex("7554")),  # ... so the left plate is lit while the bit is clear

    # The Options scales read low to high (stock: best first, stored 0, Right +1, wrapping). Each scale row's plates run from
    # its right end leftward, the preset keeping CUSTOMIZE last, and its input tests swap the Left (0x04) and Right
    # (0x08) bits so Right steps toward the best.
    *widths(0x56CDAB, 0x56CDB5, stock=140, wide=200),
    (0x56CD60, bytes.fromhex("8daffe000000"), bytes.fromhex("8daf") + p32(0xE0 + 2 * 0x8C)),  # texture filter
    (0x56CDE3, bytes.fromhex("81c58c000000"), bytes.fromhex("81ed8c000000")),
    *((va, bytes.fromhex(stock), bytes.fromhex(swapped)) for va, stock, swapped in (
        (0x56DD2B, "f6c304", "f6c308"), (0x56DD34, "f6c308", "f6c304"),  # texture filter
        (0x56E128, "f6c304", "f6c308"), (0x56E138, "f6c308", "f6c304"),  # preset
        (0x56E152, "f6c304", "f6c308"), (0x56E163, "f6c308", "f6c304"),  # particle count
        (0x56E17D, "f6c304", "f6c308"), (0x56E18B, "f6c308", "f6c304"),  # draw distance
        (0x56E1E0, "f6c304", "f6c308"), (0x56E1E9, "f6c308", "f6c304"))),  # the last HIGH MEDIUM LOW row
    # ... and stop at their ends (stored 0 is the highest, 3 CUSTOMIZE right of it on the preset)
    *scale_ends(0x56CF12, "837c240800", "837c240800", CAVE + 0x8C0, 0),  # texture filter
    *scale_ends(0x56D8A2, "837c240800", "837c240800", CAVE + 0x900, 3),  # preset
    *scale_ends(0x56C6B2, "837c240800", "837c240800", CAVE + 0x940, 0),  # characters
    *scale_ends(0x56C772, "837c240800", "837c240800", CAVE + 0x980, 0),  # fog distance
    *scale_ends(0x56D7FF, "837d0800 b903000000", "837d0800", CAVE + 0x9C0, 0, leave=0x56D838),  # shaders
    # ON/OFF rows read OFF, ON (stock: ON left). Each drawer swaps its two plates' bands and lit sheets, and each step
    # sets ON on Right and OFF on Left, doing nothing (no sound) when the row is there already. The Game Settings
    # switches step in english_runtime.c (hook 29); MOVING CAMERA FIX draws and steps there too.
    *((va, bytes.fromhex(stock), bytes.fromhex(swapped)) for va, stock, swapped in (
        (0x56C038, "bb0e000000", "bb0f000000"), (0x56C03D, "c74424140f000000", "c74424140e000000"),  # Advanced:
        (0x56C060, "c74424140e000000", "c74424140f000000"), (0x56C068, "bb0f000000", "bb0e000000"),  # lit sheets
        (0x56C08F, "6a00", "6a2a"), (0x56C0E6, "6a2a", "6a00"),  # bands: OFF first, ON second
        (0x56B6D0, "6a00", "6a2a"), (0x56B713, "6a2a", "6a00"), (0x56B6DF, "57", "55"), (0x56B727, "55", "57"),  # Game
        # Settings. The Advanced steps (arg 0 Left, 1 Right; stored 0 is ON): the new value is the arg flipped
        (0x56CFB3, "8d5801 81e301000080 7905 4b 83cbfe 43",  # DYNAMIC SHADOWS
         "8b5c240c 83f301 3bd8 7503 5e 5b c3 9090"),
        (0x56D502, "8d7001 81e601000080 7905 4e 83cefe 46",  # BLOOM
         "8b742408 83f601 3bf0 7502 5e c3 909090"))),
    *detour(0x571032, bytes.fromhex("a80c 0f8459010000"), CAVE + 0xA00, 0x57103A,  # Controller: bit 0x04 set is ON
            bytes.fromhex("a80c 0f84") + struct.pack("<i", 0x571193 - (CAVE + 0xA00 + 8))
            + bytes.fromhex("8b96e8000000 8bca c1e104 8a8c1108158000 c0e902 c0e803 32c1 a801 0f84")
            + struct.pack("<i", 0x571193 - (CAVE + 0xA00 + 42))),  # Right while ON, or Left while OFF: nothing

    *widths(0x56BFD5, 0x56BFDF, stock=140, wide=220),
    *widths(0x56C185, 0x56C18F, 0x56C21D, 0x56C227, 0x56C2BB, 0x56C2C5,
            stock=140, wide=200),
    *((va, bytes.fromhex("81c318010000"), bytes.fromhex("81c3") + p32(0xFA + 2 * 0x8C))
      for va in (0x56C12A, 0x56C1DA, 0x56C26E)),
    *((va, bytes.fromhex("81c38c000000"), bytes.fromhex("81eb8c000000")) for va in (0x56C1BD, 0x56C255, 0x56C2F3)),
    (0x56BF86, bytes.fromhex("81c318010000"), bytes.fromhex("81c3") + p32(0xF0 + 2 * 0x96)),
    *detour(0x56C00D, bytes.fromhex("81c38c000000"), CAVE + 0x880, 0x56C013,  # the preset's step, 150 leftward;
            bytes.fromhex("81eb96000000 83ff03 7506 81c3") + p32(4 * 0x96)),  # CUSTOMIZE back to slot 3

    *widths(0x585098, 0x5850A2, stock=420, wide=504),

    *widths(0x581A22, 0x581A2D, 0x581F1F, 0x581F2A, 0x581F9B, 0x581FA6, stock=168, wide=184),

    *widths(0x5850E7, 0x5850F2, stock=266, wide=432),

    (0x57261C, bytes.fromhex("be61020000"), bytes.fromhex("be3f020000")),
    (0x57265F, bytes.fromhex("8db40234feffff"), bytes.fromhex("8db40212feffff")),
    *widths(0x57267F, 0x572689, stock=476, wide=610),
    (0x572744, bytes.fromhex("6838020000"), bytes.fromhex("6816020000")),
    (0x57275C, bytes.fromhex("6851040000"), bytes.fromhex("68b5040000")),
    *widths(0x572781, stock=596, wide=656),
    *widths(0x5727AE, 0x5727B8, stock=476, wide=560),
    *widths(0x572806, 0x572810, stock=476, wide=600),

    (0x588C51, bytes.fromhex("83c04b"), bytes.fromhex("83c064")),
    *detour(0x588C92, bytes.fromhex("55 83c37c 53"), CAVE + 0x4C0, 0x588C97,
            bytes.fromhex("55 81c395000000 53")),
    (0x588D81, bytes.fromhex("81c3a6000000"), bytes.fromhex("81c3bf000000")),

    (0x538C6A, bytes.fromhex("8d50018d49008a0883c00184c975f72bc2d1e88d0c408d74ce02"),
     bytes.fromhex("6a006a0050e80c33010083c40c8d740602909090909090909090")),
    (0x588ACF, bytes.fromhex("8d50018a0883c00184c975f72bc2d1e88d0c408d5ccb03"),
     bytes.fromhex("6a006a0050e8a734fcff83c40c8d5c0303909090909090")),

    *widths(0x59DCF7, 0x59DD02, stock=308, wide=372),

    *((va + 6, bytes([stock]), b"\x09") for va, stock in
      ((0x4761CE, 2), (0x4761E9, 2), (0x476206, 1), (0x476233, 1), (0x47624F, 0))),

    *widths(0x596839, stock=0x330, wide=982),
    *[(va, bytes.fromhex("68e7000000"), bytes.fromhex("6894000000")) for va in (0x596845, 0x596854, 0x596865, 0x596877)],
    (0x596890, bytes.fromhex("8d515d"), bytes.fromhex("8d510a")),
    (0x5968A1, bytes.fromhex("8d485d"), bytes.fromhex("8d480a")),

    (0x596666, bytes.fromhex("8b0d7cec7900"), rel32(b"\xe9", 0x596666, CAVE + 0x620) + b"\x90"),
    (CAVE + 0x620, b"\xcc" * 32, b"\x80\x3d" + p32(PAD_PROMPTS) + bytes.fromhex("00 750b 8b0d7cec7900")
     + rel32(b"\xe9", CAVE + 0x62F, 0x59666C) + bytes.fromhex("8b8c2484000000") + rel32(b"\xe9", CAVE + 0x63B, 0x5967D9)),

    *widths(0x595F17, stock=308, wide=568),
    (0x582619, bytes.fromhex("81c672020000"), bytes.fromhex("81c6f0010000")),
    (0x553885, bytes.fromhex("68e9010000"), bytes.fromhex("6867010000")),

    (0x597512, bytes.fromhex("68b2010000"), bytes.fromhex("68d1010000")),
    *widths(0x597517, stock=532, wide=745),
    (0x5975BB, bytes.fromhex("6805020000"), b"\x68" + p32(482 + ABILITY_CELL_GROWTH)),
    *widths(0x5975CF, 0x59761D, stock=452, wide=686),
    (0x597624, bytes.fromhex("6879010000"), bytes.fromhex("6899010000")),
    *[(va, bytes.fromhex("6886010000"), bytes.fromhex("68cc010000")) for va in (0x597675, 0x5976A2)],
    (0x59767A, bytes.fromhex("68d1010000"), bytes.fromhex("685d020000")),
    (0x5976A7, bytes.fromhex("68f1010000"), bytes.fromhex("685d020000")),
    (0x5976D1, bytes.fromhex("6886010000"), bytes.fromhex("68a5010000")),
    (0x596E9E, bytes.fromhex("81c151010000"), bytes.fromhex("81c171010000")),
    # Conditions STATUS rows as X360 draws them: plates 573 wide and values right-aligned at x+681. The time
    # and KOs rows draw no "Left" (stock sized it at 12 px a byte, so English "Left" ran under the count);
    # the gauge rows keep "OK", with room for it in row_columns.
    *[(va, bytes.fromhex("6868010000"), bytes.fromhex("683d020000"))
      for va in (0x596F82, 0x5970E0, 0x59721B, 0x597347, 0x597468)],
    *[(va, bytes.fromhex("81c1d4010000"), bytes.fromhex("81c1a9020000")) for va in (0x597051, 0x5973E8)],
    # The count font steps 21 px a character, as X360 reserves it; stock reserves 12, so each digit pushed the
    # count 9 px right. The KOs counts ("%d?") end in a blank glyph, so their digits end at x+681 from x+702.
    *[(va, bytes.fromhex("8d3c40"), bytes.fromhex("6bf815")) for va in (0x59701F, 0x59715B, 0x59728D)],
    *[(va, bytes.fromhex("03ff"), bytes.fromhex("8bff"))
      for va in (0x597031, 0x597037, 0x597176, 0x59717C, 0x5972A8, 0x5972AE)],
    *[(va, bytes.fromhex("81c1d4010000"), bytes.fromhex("81c1be020000")) for va in (0x597196, 0x5972C8)],
    (0x597402, bytes.fromhex("8d8fd4010000"), bytes.fromhex("8d8fa9020000")),  # the Life Gauge percent
    (0x5974BF, bytes.fromhex("81c5d4010000"), bytes.fromhex("81c5a9020000")),  # the Musou Gauge "OK"
    (0x597045, bytes.fromhex("e88647fbff"), bytes.fromhex("0f1f440000")),  # the args go with the row's later cleanup
    (0x59718A, bytes.fromhex("e84146fbff"), bytes.fromhex("0f1f440000")),
    (0x5972BC, bytes.fromhex("e80f45fbff"), bytes.fromhex("0f1f440000")),

    (0x598099, bytes.fromhex("83e05b"), bytes.fromhex("83e07b")),
    (0x5980A1, bytes.fromhex("05c4000000"), bytes.fromhex("05f4000000")),
    (0x59812F, bytes.fromhex("83c638"), bytes.fromhex("83c654")),
    (0x5981A3, bytes.fromhex("81c7d2000000"), bytes.fromhex("81c70a010000")),

    (0x598221, bytes.fromhex("81c6f5000000"), bytes.fromhex("81c66d010000")),
]

def immediate(va: int, opcode: str, stock: int, x360: int) -> tuple:
    """Return a checked edit of the 32-bit immediate that ends an instruction."""
    return (va, bytes.fromhex(opcode) + p32(stock & 0xFFFFFFFF), bytes.fromhex(opcode) + p32(x360 & 0xFFFFFFFF))

def imm8(va: int, opcode: str, stock: int, x360: int) -> tuple:
    """Return a checked edit of the signed 8-bit immediate or displacement that ends an instruction."""
    return (va, bytes.fromhex(opcode) + bytes([stock & 0xFF]), bytes.fromhex(opcode) + bytes([x360 & 0xFF]))

def cell_then(site: int, stock: str, cave: int, cell: int, field: int, skip: int) -> list:
    """Detour `mov eax,[esi+field]; test eax,eax; je skip` to set the font cell width first."""
    code = bytes.fromhex("c6050caec200") + bytes([cell, 0x8B, 0x46, field, 0x85, 0xC0])
    code += b"\x0f\x84" + struct.pack("<i", skip - (cave + len(code) + 6))
    return detour(site, bytes.fromhex(stock), cave, site + len(bytes.fromhex(stock)), code)

# Panel geometry taken from the X360 build of the same screen.
X360_LAYOUT = [
    # Still-event bar x, y, width, height, and the name plate and name y above it.
    immediate(0x59C728, "68", 232, 40), immediate(0x59C723, "68", 412, 503),
    immediate(0x59C71C, "68", 756, 1200), immediate(0x59C717, "68", 262, 126),
    immediate(0x59C433, "68", 398, 440), immediate(0x59C4A2, "68", 412, 454),
    # Battle history: box, scroll bar, two entries, time and text x, lone entry, highlight, row table.
    immediate(0x53A631, "68", 293, 237), immediate(0x53A62C, "68", 424, 430), immediate(0x53A625, "68", 708, 812),
    immediate(0x53A663, "68", 292, 236), immediate(0x53A65E, "68", 426, 432),
    (0x53A676, bytes.fromhex("83f803"), bytes.fromhex("83f802")), (0x53A764, bytes.fromhex("83ff03"), bytes.fromhex("83ff02")),
    *widths(0x53A68B, 0x53A73D, stock=307, wide=272), immediate(0x539F94, "68", 314, 279),
    immediate(0x53A686, "68", 500, 531),
    immediate(0x53A7AC, "68", 314, 265), immediate(0x53A7A7, "68", 522, 553), immediate(0x53A7A0, "68", 673, 764),
    (0x53A79E, bytes.fromhex("6a23"), bytes.fromhex("6a46")),
    # Movie subtitle cell and pitch, and the two line bases.
    (0x49EAA1, bytes.fromhex("b020"), bytes.fromhex("b018")),
    (0x49EABF, bytes.fromhex("c6050eaec20024"), bytes.fromhex("c6050eaec2001c")),
    immediate(0x49EAFE, "b8", 628, 652), immediate(0x49EB39, "b8", 664, 680),
]

# X360 panels: lists, cards, plates and boxes of the same screens.
X360_PANELS = [
    # Officer list (0x595AA0) and gallery lists (0x556D60, 0x55B540) at the X360 width,
    # their callers' x and slides, and neighbour panels.
    immediate(0x595AC1, "68", 225, 321),
    imm8(0x595B3D, "83c2", 25, 35),
    imm8(0x595B6E, "8d47", 17, 22),
    imm8(0x595B71, "83c7", 28, 29),
    imm8(0x595B83, "83c7", 32, 42),
    immediate(0x595BB6, "68", 189, 296),
    immediate(0x556D81, "68", 215, 321),
    immediate(0x556E7A, "68", 189, 301),
    imm8(0x55B55A, "8d45", 33, 32),
    immediate(0x55B55E, "68", 209, 321),
    imm8(0x55B578, "83c6", -8, -9),
    imm8(0x55B5DA, "83c2", 25, 31),
    imm8(0x55B612, "83c7", 28, 23),
    immediate(0x55B656, "68", 189, 301),
    immediate(0x56A7A1, "69c9", 483, 579),
    immediate(0x56A7B5, "8dbc02", -225, -321),
    immediate(0x529587, "69c9", 523, 576),
    immediate(0x52959B, "8d9c02", -225, -321),
    immediate(0x5295B0, "69c9", 710, 645),
    immediate(0x5295C4, "8d9c0a", -412, -390),
    immediate(0x5295DC, "bb", 298, 255),
    immediate(0x52D757, "69d2", 524, 555),
    immediate(0x52D76B, "8db402", -225, -321),
    immediate(0x52D77E, "69d2", 903, 806),
    immediate(0x52D792, "8db402", -604, -572),
    immediate(0x52D7A4, "be", 299, 234),
    immediate(0x527705, "69c9", 430, 454),
    immediate(0x527719, "8d840a", -203, -267),
    immediate(0x527722, "b8", 227, 187),
    immediate(0x52776D, "bb", 474, 482),
    immediate(0x5277C7, "69c9", 806, 798),
    immediate(0x527882, "bf", 741, 831),
    immediate(0x527890, "69ff", 806, 798),
    immediate(0x5278A8, "b8", 1547, 1629),
    immediate(0x55742F, "69c9", 463, 537),
    immediate(0x557443, "8d9c0a", -215, -321),
    immediate(0x557456, "bb", 248, 216),
    immediate(0x557506, "69c9", 796, 710),
    immediate(0x557529, "c7442410", 484, 570),
    immediate(0x5577C0, "bd", 508, 594),  # the selection frame's column-0 x follows the moved thumbnails
    # The description bar, its text and the page arrow under the moved thumbnail panel, as in stock.
    immediate(0x5579F7, "bd", 484, 570), immediate(0x557AAF, "68", 484, 570), immediate(0x557B6D, "68", 484, 570),
    immediate(0x557AE8, "68", 508, 594), immediate(0x557B92, "68", 508, 594), immediate(0x557BA9, "68", 508, 594),
    immediate(0x55BAF0, "69c9", 471, 538),
    immediate(0x55BB04, "8dbc02", -225, -321),
    immediate(0x55BB15, "bf", 246, 217),
    immediate(0x55BBE7, "69f6", 782, 699),
    immediate(0x55BC0A, "c7442414", 498, 581),
    imm8(0x595ADE, "8d57", 35, 82),
    imm8(0x595AF2, "8d47", 73, 120),
    immediate(0x595B0F, "8d97", 157, 207),
    imm8(0x556D9F, "8d4e", 35, 82),
    imm8(0x556DB7, "8d46", 73, 120),
    immediate(0x556DD5, "8d86", 157, 207),
    imm8(0x55B57B, "8d57", 35, 92),
    *detour(0x55B58C, bytes.fromhex("6a46538d4749"), 0x5F5E00, 0x55B592, bytes.fromhex("6a46538d8782000000")),
    immediate(0x55B5AC, "8d97", 157, 217),
    imm8(0x56A859, "83c7", 73, 120),
    imm8(0x529666, "83c3", 73, 120),
    imm8(0x52D82A, "83c6", 73, 121),
    imm8(0x527832, "83c3", 73, 120),
    imm8(0x5574F0, "83c3", 73, 120),
    *detour(0x55BBA3, bytes.fromhex("689200000083c749"), 0x5F5E20, 0x55BBAB, bytes.fromhex("689100000081c782000000")),
    # Attribute list panel and slide; skill list panel, grade row and slides. The weapon row keeps
    # its stock name x: MOZ draws the weapon thumbnail at row x+0x26..+0x5E, where the X360 name starts.
    immediate(0x52E5CD, "68", 378, 391),
    immediate(0x52F9A2, "b9", 223, 227),
    imm8(0x52F9A9, "8d69", -95, -99),
    immediate(0x52F9BA, "69c9", 601, 618),
    immediate(0x52F9CE, "8d8c02", -378, -391),
    imm8(0x52FA17, "6a", 122, 121),
    immediate(0x5332F8, "68", 203, 267),
    immediate(0x53343A, "68", 189, 253),
    imm8(0x533315, "8d4e", 21, 48),
    immediate(0x53332F, "8d86", 161, 188),
    imm8(0x533349, "8d4e", 56, 83),
    imm8(0x533372, "8d56", 66, 93),
    immediate(0x527418, "69c9", 510, 454),
    immediate(0x52742C, "8dbc02", -203, -267),
    immediate(0x527464, "bf", 307, 187),
    immediate(0x528F05, "69c9", 511, 494),
    immediate(0x528F19, "8d8c0a", -203, -267),
    immediate(0x528F27, "b9", 308, 227),
    # Pre-battle army list (0x536630) and its slide; officer card box, no reading line.
    immediate(0x536654, "68", 245, 335),
    imm8(0x536675, "8d4d", 7, 12),
    imm8(0x53669A, "8d45", 42, 47),
    immediate(0x536694, "68", 147, 217),
    immediate(0x5366FD, "b8", 147, 217),
    imm8(0x53670E, "8d4428", 42, 47),
    immediate(0x536791, "8d8d", 203, 285),
    immediate(0x536828, "68", 189, 280),
    immediate(0x5368C4, "81c2", 210, 307),
    immediate(0x5368F3, "05", 210, 307),
    immediate(0x536970, "69d2", 351, 391),
    immediate(0x536984, "8db402", -245, -335),
    immediate(0x53699B, "be", 106, 56),
    imm8(0x535E6C, "8d53", -8, -22),
    immediate(0x535E5D, "68", 294, 413),
    (0x535EC1, bytes.fromhex("e80a590100"), bytes.fromhex("0f1f440000")),
    imm8(0x535EDB, "8d4e", 24, 12),
    # Pre-battle army line cell and TIME plate; battle banner backing and label cell.
    imm8(0x538C4E, "6a", 24, 22),
    immediate(0x538CB0, "68", 858, 942),
    immediate(0x538CA3, "68", 154, 70),
    immediate(0x538C9C, "68", 318, 234),
    immediate(0x54FE19, "81c6", 409, 528),
    imm8(0x550821, "6a", 24, 22),
    # Still-event plates (616x70, both X360 plates in one texture) and the scenario panel,
    # RECORD at the top right.
    immediate(0x59C3DF, "bf", 232, 26),
    immediate(0x59C3E6, "bf", 787, 640),
    imm8(0x59C41F, "6a", 52, 70),
    imm8(0x59C42A, "6a", 52, 70),
    immediate(0x59C42C, "68", 266, 616),
    *detour(0x59C421, bytes.fromhex("680a0100006a006a00"), 0x5F5E40, 0x59C42A, bytes.fromhex("686802000031c083ff7f7c02b080506a00")),
    # Z-story still event bar at the X360 x and width (stock source rectangle stretched).
    immediate(0x59A712, "68", 192, 40), immediate(0x59A706, "68", 896, 1200),
    # Captions, movie title and body cells.
    immediate(0x56A160, "bb", 600, 616),
    immediate(0x56A1A8, "c706", 636, 652),
    *cell_then(0x49EAE4, "8b4624 85c0 7434", 0x5F5F00, 29, 0x24, 0x49EB1F),
    *cell_then(0x49EB1F, "8b4620 85c0 7434", 0x5F5F40, 24, 0x20, 0x49EB5A),
]

# Screens that keep the MOZ layout (the pre-wave-4 panels): VS and Survival rules panel moved left and
# widened, the weapon rows and their skill squares widened, the treasure list and detail panel, the
# narration panel. The gallery weapon list (0x55B540) keeps its X360 plate with the name centred in it
# as MOZ centres it (plate at name y - 7).
MOZ_LAYOUT = [
    (0x5A66F3, bytes.fromhex("bb1a020000"), bytes.fromhex("bb26010000")),
    (0x5A6700, bytes.fromhex("681a020000"), bytes.fromhex("6826010000")),
    *widths(0x5A6783, stock=641, wide=906),
    (0x580780, bytes.fromhex("beb6010000"), bytes.fromhex("beab000000")),
    (0x58076A, bytes.fromhex("68b6010000"), bytes.fromhex("68ab000000")),
    *widths(0x580791, stock=641, wide=906),
    *widths(0x59506C, stock=350, wide=478),
    (0x595135, bytes.fromhex("8dbc3918010000"), bytes.fromhex("8dbc3998010000")),
    *widths(0x5951E6, stock=366, wide=494),
    *widths(0x526DE4, stock=378, wide=506),
    *widths(0x554387, stock=376, wide=504),
    *widths(0x552E76, stock=212, wide=362), *widths(0x552F09, stock=189, wide=339),
    *widths(0x552F7F, stock=392, wide=472),
    (0x553139, bytes.fromhex("69c96c010000"), bytes.fromhex("69c949010000")),
    (0x55315E, bytes.fromhex("bfec000000"), bytes.fromhex("bfc9000000")),
    (0x5531BB, bytes.fromhex("69c9d4020000"), bytes.fromhex("69c9a1020000")),
    (0x5531DA, bytes.fromhex("bf2c020000"), bytes.fromhex("bf5f020000")),
    *widths(0x56108C, stock=756, wide=1229),
    *widths(0x561098, stock=262, wide=25),
    *widths(0x561087, stock=262, wide=305),
    imm8(0x55B624, "83c7", 32, 38),
]

# Our own values on X360-layout screens. Battle history: 4 px more between the time and the event text
# (lone entry and each entry), the second entry 4 px lower and the box and scroll bar 8 px taller.
# PC controls list: action cells 40 px wider, keyboard and pad columns moved with them, so the widest
# action name fits its lane at full size.
LAYOUT_TWEAKS = [
    immediate(0x53A699, "68", 528, 563),
    imm8(0x53A74E, "83c3", 28, 32),
    (0x71029C, p32(272), p32(292)),
    immediate(0x53A620, "68", 188, 196),
    immediate(0x53A655, "68", 182, 190),
    *widths(0x598395, stock=257, wide=297),
    *widths(0x5982D3, 0x59840D, stock=541, wide=581),
    *widths(0x598495, 0x5984E5, stock=497, wide=537),
    *widths(0x5984DD, stock=556, wide=516),
    *widths(0x59853F, stock=535, wide=575),
    *widths(0x598534, stock=507, wide=467),
]

# Page text drawn away from its stock origin. The Z-story narration glyph callback (0x55EDC0) adds 4 px
# per glyph from a counter it resets only when a glyph starts at the stock line x 0x168; at the X360
# origin the counter never resets and the text runs off screen, so the glyphs keep their plain advance.
PAGE_ORIGIN = [
    (0x55EEAD, bytes.fromhex("8d4c8a02"), bytes.fromhex("8d4a0290")),
]

# Two-line captions: the cue layout (0x56A130) gives both lines the smaller of their centred x, so the
# shorter line sat left of centre; each line keeps its own centred x.
CAPTION_LINES = [
    (0x56A1DC, bytes.fromhex("890e"), bytes.fromhex("9090")),
    (0x56A1E2, bytes.fromhex("890a"), bytes.fromhex("9090")),
]

# The attribute cell (0x591810, widened above) is 150 wide, not 98, wherever it is drawn: each grid of them steps
# 156 and draws its empty slots (0x5916B0) 150 wide too, on the weapon card (0x595310; its step is widened above)
# and in the upgrade skill effects (0x526B70).
ATTRIBUTE_GRIDS = [
    *detour(0x59556B, bytes.fromhex("6a1c 6a62 53 52 51"), CAVE + 0x1C0, 0x595572,
            bytes.fromhex("6a1c 6896000000 53 52 51")),
    *detour(0x526C51, bytes.fromhex("6bc969 99 83e203"), CAVE + 0x1E0, 0x526C58,
            bytes.fromhex("69c99c000000 99 83e203")),
    *detour(0x526C98, bytes.fromhex("6a1c 6a62 56 52 51"), CAVE + 0x200, 0x526C9F,
            bytes.fromhex("6a1c 6896000000 56 52 51")),
]

# Skill tags are TAG_WIDTH wide (runtime hook 108 draws them on 0x593CE0's calls), so every row of three
# on a weapon card steps TAG_STEP: the card itself (0x595310), the fusion overlay over it (0x52E940, both paths) and
# the upgrade's tag rows over it (0x528E40, modes 6 and 7/9, which end at the third tag and frame the chosen one).
TAG_WIDTH = 138
TAG_STEP = TAG_WIDTH + 7
SKILL_TAGS = [
    *detour(0x5954B9, bytes.fromhex("83c601 83c414 83c54d"), CAVE + 0x220, 0x5954C2,
            bytes.fromhex("83c601 83c414 81c5") + p32(TAG_STEP)),
    *detour(0x52EA97, bytes.fromhex("83c34d 83fd03"), CAVE + 0x240, 0x52EA9D,
            bytes.fromhex("81c3") + p32(TAG_STEP) + bytes.fromhex("83fd03")),
    *detour(0x52EBC1, bytes.fromhex("83c704 83c64d"), CAVE + 0x260, 0x52EBC7,
            bytes.fromhex("83c704 81c6") + p32(TAG_STEP)),
    *detour(0x52913C, bytes.fromhex("6a1c 6a46 6a00"), CAVE + 0x280, 0x529142,
            bytes.fromhex("6a1c 68") + p32(TAG_WIDTH) + bytes.fromhex("6a00")),
    *detour(0x529150, bytes.fromhex("83c74d 83c501"), CAVE + 0x2A0, 0x529156,
            bytes.fromhex("81c7") + p32(TAG_STEP) + bytes.fromhex("83c501")),
    *detour(0x5291EB, bytes.fromhex("83c74d 83c414"), CAVE + 0x2C0, 0x5291F1,
            bytes.fromhex("81c7") + p32(TAG_STEP) + bytes.fromhex("83c414")),
    *[(va, bytes.fromhex("81ff2a040000"), b"\x81\xff" + p32(0x343 + 3 * TAG_STEP)) for va in (0x529156, 0x5291F4)],
]

# Kept art and clearance edits that the X360 panels replace.
X360_REPLACED = {
    0x595ADE, 0x595AEF, 0x595AF2, 0x595B0F, 0x527821, 0x527829, 0x527832, 0x529655, 0x52965D, 0x529666,
    0x52D819, 0x52D821, 0x52D82A, 0x56A848, 0x56A850, 0x56A859, 0x556D9F, 0x556DB4, 0x556DB7, 0x556DD5,
    0x55B57B, 0x55B58C, 0x55B58F, 0x55B5AC, 0x5574DF, 0x5574E7, 0x5574F0, 0x55BB9A, 0x55BBA0, 0x55BBA8,
    0x52B5B7, 0x52B5DA, 0x52B5E1, 0x52B5F2, 0x52BB4A, 0x52BB52, 0x52BB5B,
    0x533315, 0x53332F, 0x533349, 0x533372, 0x533346, 0x533365, 0x53336D,
    0x59C421, 0x59C42C, 0x59C48E, 0x59C3DF, 0x59C3E6,
}
CODE_EDITS = ([edit for edit in CODE_EDITS if edit[0] not in X360_REPLACED] + MOZ_LAYOUT + X360_LAYOUT + X360_PANELS
              + LAYOUT_TWEAKS + PAGE_ORIGIN + CAPTION_LINES + ATTRIBUTE_GRIDS + SKILL_TAGS)

LATIN_TOKEN = 0x1000

def _fullwidth(byte: int) -> int:
    return {0x20: 0x8140, 0x22: 0x814A, 0x27: 0x8166}.get(byte) or int.from_bytes(
        chr(byte + 0xFEE0).encode("cp932"), "big")

FONT_ROUTE = [
    (0x549A49, bytes.fromhex("752a"), bytes.fromhex("9090")),
    (0x549A4B, bytes.fromhex("8b35bcba7900 8b0db8ba7900 33c0 85f6 7665 8a54240c 3a140874 61 83c001 3bf0 77f4"
                             " 5f b808000000 5e c3 3c02 7549"),
     # movzx eax, word [esp+0Ch]; a Latin token or a surrogate code becomes its glyph number, else the stock lookup
     bytes.fromhex("0fb744240c 2d") + p32(LATIN_TOKEN) + bytes.fromhex(f"83f8{LATIN_COUNT - 1:02x} 7707 05")
     + p32(LATIN_GLYPH) + bytes.fromhex("eb66 2d") + p32(SURROGATE_CODE - LATIN_TOKEN)
     + bytes.fromhex(f"83f8{len(SURROGATE_LETTERS) - 1:02x} 770e 05") + p32(SURROGATE_GLYPH)
     + bytes.fromhex("eb55" + " 90" * 7)),

    *[(0x79BA98 + 2 * b, struct.pack("<H", _fullwidth(b)), struct.pack("<H", LATIN_TOKEN + b - LATIN_FIRST))
      for b in range(LATIN_FIRST, LATIN_FIRST + LATIN_COUNT) if b not in b"\\`{|}"],
]

def settings_rows(rows: int, distance: bool = False, moving_fix: bool = False) -> list:
    """The cursor list: the stock rows, the language rows, FOV, frame rate and, in dev builds only, the moving camera
    fix and render distance."""
    count = 6 + rows + moving_fix + distance
    return [

        (0x56B2ED, bytes.fromhex("015010 c740140b000000 c7401800000000"),
         bytes.fromhex(f"6a{count:02x} 8f4010 c740140b000000 83601800 90")),
        (0x56B60B, bytes.fromhex("83ea01"), bytes([0x83, 0xEA, count - 3])),
        (0x56B750, bytes.fromhex("83ea01"), bytes([0x83, 0xEA, count - 3])),
        (0x56B7E4, bytes.fromhex("83fe03"), bytes([0x83, 0xFE, count - 1])),
        (0x56B806, bytes.fromhex("83fe03"), bytes([0x83, 0xFE, count - 1])),

        (0x56B86A, bytes.fromhex("83c603 83c414 81e603000080 7905 4e 83cefc 46"),
         bytes.fromhex("83c414 4e 7904 8b7510 4e") + b"\x90" * 9),
        (0x56B8AB, bytes.fromhex("83c601 83c414 81e603000080 7905 4e 83cefc 46"),
         bytes.fromhex("83c414 46 3b7510 7c02 33f6") + b"\x90" * 8),
    ]


# The game sizes its sound pool once at boot (_aligned_malloc at 0x5C9E43, the pool's allocator at 0x5CA4D8), and
# every loaded sound-effect bank stays in it (0x66DCC0). 0x6300000 holds the stock ADPCM banks; the English banks
# are 16-bit PCM, up to 0x6B0E0D0 bytes more if all were loaded, and a full pool crashes the game at battle start.
STOCK_SOUND_POOL = 0x6300000
SOUND_POOL = 0xCF00000
VOICE_EDITS = [(va, b"\x68" + p32(STOCK_SOUND_POOL), b"\x68" + p32(SOUND_POOL)) for va in (0x5C9E43, 0x5CA4D8)]

# The Xbox defaults (the pad auto-setting's layout 3, block 0x5E378D) bind Camera Up, Down, Left and Right (actions
# 17-20) to the right stick instead of none: axis 4 is lRy, axis 3 is lRx, which the runtime copies into the lRz
# slot the stock poll reads. Direction 1 keeps an axis's negative half (up, left). Each action's block is 0x41
# bytes and pushes its pad binding's isAxis at +0x22, direction at +0x2D and index at +0x38.
PAD_CAMERA = [
    (0x5E378D + 0x41 * n + at, bytes([0x6A, stock]), bytes([0x6A, value]))
    for n, (direction, axis) in enumerate([(1, 4), (0, 4), (1, 3), (0, 3)])
    for at, stock, value in [(0x22, 0, 1), (0x2D, 0, direction), (0x38, 0xFF, axis)]
    if value != stock
]

# A new save and the Controller page's DEFAULT start CAMERA - HORIZONTAL at STANDARD: bit 0x1 of the player's options
# byte (0x801508 + 0x11 * player) set, so the stick camera (0x4AF600) turns the view the way the stick is pushed. Stock
# clears it, INVERSE. The first-run defaults (0x475440, from 0x476FF0) clear bits 0-2 for both players before any save
# is copied over them, so a loaded save keeps its own value.
STANDARD_CAMERA = [
    *detour(0x47544C, bytes.fromhex("24f8 6a01 6a00"), CAVE + 0xA40, 0x475452,  # player 1: and al, 0xF8; or al, 1
            bytes.fromhex("24f8 0c01 6a01 6a00")),
    *detour(0x475468, bytes.fromhex("24f8 6a01 6a01"), CAVE + 0xA60, 0x47546E,  # player 2
            bytes.fromhex("24f8 0c01 6a01 6a01")),
    (0x570C98, bytes.fromhex("80a40208158000fe"), bytes.fromhex("808c020815800001")),  # DEFAULT: or 1, not and 0xFE
]

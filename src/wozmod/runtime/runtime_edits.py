"""Fixed stock-byte checks and x86 runtime edits."""

from __future__ import annotations

import struct

CAVE = 0x5F5100
FUSION_PAGE = 0x1413FF0
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

CODE_EDITS = [
    (0x5695C5, bytes.fromhex("8b0783f802"), bytes.fromhex("e98b000000")),

    (0x560A5E, bytes.fromhex("c70554f2c20004000000"), bytes.fromhex("c70554f2c20002000000")),

    *widths(0x55453E, 0x554549, 0x531931, 0x53193C, 0x53C8DB, 0x53C8E6,
            0x5845E8, 0x5845F3, 0x591C6C, 0x591C81, 0x5988DA, 0x5988E5,
            stock=336, wide=340),

    (0x525F85, bytes.fromhex("68c4000000"), bytes.fromhex("6800010000")),
    (0x525F8F, bytes.fromhex("68c4000000"), bytes.fromhex("6800010000")),

    *widths(0x533DD1, 0x533DDF, stock=196, wide=288),

    *widths(0x533ABE, 0x533AC8, stock=322, wide=556),

    *widths(0x595AC1, stock=225, wide=361), *widths(0x595BB6, stock=189, wide=325),
    (0x595ADE, bytes.fromhex("8d5723"), bytes.fromhex("8d5759")),
    (0x595AEF, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x595AF2, bytes.fromhex("8d4749"), bytes.fromhex("8d477f")),
    (0x595B0F, bytes.fromhex("8d979d000000"), bytes.fromhex("8d97ed000000")),
    *[(va, bytes.fromhex("6a46"), bytes.fromhex("6a60"))
      for va in (0x527821, 0x527829, 0x529655, 0x52965D, 0x52D819, 0x52D821, 0x56A848, 0x56A850)],
    (0x527832, bytes.fromhex("83c349"), bytes.fromhex("83c37f")),
    (0x529666, bytes.fromhex("83c349"), bytes.fromhex("83c37f")),
    (0x52D82A, bytes.fromhex("83c649"), bytes.fromhex("83c67f")),
    (0x56A859, bytes.fromhex("83c749"), bytes.fromhex("83c77f")),

    (0x529587, bytes.fromhex("69c90b020000"), bytes.fromhex("69c94f020000")),
    (0x52959B, bytes.fromhex("8d9c021fffffff"), bytes.fromhex("8d9c0297feffff")),
    (0x5295B0, bytes.fromhex("69c9c6020000"), bytes.fromhex("69c982020000")),
    (0x5295DC, bytes.fromhex("bb2a010000"), bytes.fromhex("bbe6000000")),
    (0x52D757, bytes.fromhex("69d20c020000"), bytes.fromhex("69d24f020000")),
    (0x52D76B, bytes.fromhex("8db4021fffffff"), bytes.fromhex("8db40297feffff")),
    (0x52D77E, bytes.fromhex("69d287030000"), bytes.fromhex("69d242030000")),
    (0x52D7A4, bytes.fromhex("be2b010000"), bytes.fromhex("bee6000000")),
    (0x56A7A1, bytes.fromhex("69c9e3010000"), bytes.fromhex("69c931020000")),
    (0x56A7B5, bytes.fromhex("8dbc021fffffff"), bytes.fromhex("8dbc0297feffff")),
    (0x56A7D0, bytes.fromhex("bf02010000"), bytes.fromhex("bfc8000000")),

    (0x527705, bytes.fromhex("69c9ae010000"), bytes.fromhex("69c92b010000")),
    (0x527722, bytes.fromhex("b8e3000000"), bytes.fromhex("b830000000")),
    (0x52776D, bytes.fromhex("bbda010000"), bytes.fromhex("bb52010000")),
    (0x5277C7, bytes.fromhex("69c926030000"), bytes.fromhex("69c9ae030000")),

    (0x52B5B7, bytes.fromhex("8d4b18"), bytes.fromhex("8d4b0b")),
    (0x52B5DA, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x52B5E1, bytes.fromhex("8d433f"), bytes.fromhex("8d4332")),
    (0x52B5F2, bytes.fromhex("8d8b9d000000"), bytes.fromhex("8d8baa000000")),
    (0x52BB4A, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x52BB52, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x52BB5B, bytes.fromhex("83c73f"), bytes.fromhex("83c732")),

    *widths(0x5332F8, stock=203, wide=251), *widths(0x53343A, stock=189, wide=237),
    *detour(0x53338F, bytes.fromhex("8d4e07 8d5611 83c665"), CAVE + 0x1C0, 0x533398,
            bytes.fromhex("8d4e07 8d5611 81c6b8000000")),
    (0x533315, bytes.fromhex("8d4e15"), bytes.fromhex("8d4e0b")),
    (0x53332F, bytes.fromhex("8d86a1000000"), bytes.fromhex("8d86dc000000")),
    (0x533346, bytes.fromhex("6a5b"), bytes.fromhex("6a70")),
    (0x533349, bytes.fromhex("8d4e38"), bytes.fromhex("8d4e46")),
    (0x533365, bytes.fromhex("6a46"), bytes.fromhex("6a70")),
    (0x53336D, bytes.fromhex("6a46"), bytes.fromhex("6a70")),
    (0x533372, bytes.fromhex("8d5642"), bytes.fromhex("8d5646")),

    (0x52742C, bytes.fromhex("8dbc0235ffffff"), bytes.fromhex("8dbc0205ffffff")),
    (0x527464, bytes.fromhex("bf33010000"), bytes.fromhex("bf03010000")),
    (0x527719, bytes.fromhex("8d840a35ffffff"), bytes.fromhex("8d840a05ffffff")),
    (0x528F19, bytes.fromhex("8d8c0a35ffffff"), bytes.fromhex("8d8c0a05ffffff")),
    (0x528F27, bytes.fromhex("b934010000"), bytes.fromhex("b904010000")),

    *widths(0x52D072, 0x52D08C, 0x59858A, 0x598DFC, stock=0xE0, wide=0xF6),
    *widths(0x5955BB, 0x52D077, 0x52D091, 0x53375B, 0x53376D, 0x59858F, 0x598E01, stock=0x1D8, wide=650),

    (0x598DD3, bytes.fromhex("69f6bb020000"), bytes.fromhex("69f6bd020000")),
    (0x598DE7, bytes.fromhex("8db40228feffff"), bytes.fromhex("8db40276fdffff")),
    (0x598DF0, bytes.fromhex("bee3000000"), bytes.fromhex("be33000000")),

    *[edit for site, stub, target in ((0x52D3CF, CAVE + 0x5A0, 0x533740), (0x52D663, CAVE + 0x5C0, 0x5955A0))
      for edit in ((site, rel32(b"\xe8", site, target), rel32(b"\xe8", site, stub)),
                   (stub, b"\xcc" * 32, assembled(stub, b"\x8f\x05" + p32(FUSION_PAGE + 4)
                                                  + b"\xc6\x05" + p32(FUSION_PAGE) + b"\x01", target,
                                                  b"\xc6\x05" + p32(FUSION_PAGE) + b"\x00\xff\x35"
                                                  + p32(FUSION_PAGE + 4) + b"\xc3")))],

    *[(va, bytes.fromhex("68e0000000"), rel32(b"\xe8", va, CAVE + 0x5E0)) for va in (0x5955B6, 0x533756, 0x533768)],
    (CAVE + 0x5E0, b"\xcc" * 29, bytes.fromhex("ff3424 c7442404f6000000 803d") + p32(FUSION_PAGE)
     + bytes.fromhex("00 7408 c7442404e0000000 c3")),
    *detour(0x5954A3, bytes.fromhex("8b442468 6a00 53 83c06d 50"), CAVE + 0x300, 0x5954AE,
            bytes.fromhex("69eeb6000000 036c2464 83c513 8b442468 0586000000 803d") + p32(FUSION_PAGE)
            + bytes.fromhex("00 7410 6bee7d 036c2464 81c5bb000000 83e819 6a00 53 50")),
    *[(va, bytes.fromhex(f"8d{m}8f000000"), bytes.fromhex(f"8d{m}a6000000"))
      for va, m in ((0x52EB02, "9410"), (0x52EC31, "9c08"), (0x59863F, "8428"))],
    *detour(0x595541, bytes.fromhex("8d94108f000000"), CAVE + 0x600, 0x595548,
            bytes.fromhex("8d9410a6000000 803d") + p32(FUSION_PAGE) + bytes.fromhex("00 7403 83ea17")),

    *[edit for init, push in ((0x5290EA, 0x5290F8), (0x5291CC, 0x5291DF)) for edit in (
        (init, bytes.fromhex("bf43030000"), bytes.fromhex("bf86020000")),
        (push, bytes.fromhex("680d010000"), bytes.fromhex("6826010000")))],
    (0x529150, bytes.fromhex("83c74d 83c501 81ff2a040000"), bytes.fromhex("81c7b6000000 83c501 83fd03")),
    *detour(0x5291EB, bytes.fromhex("83c74d 83c414"), CAVE + 0x580, 0x5291F1, bytes.fromhex("81c7b6000000 83c414")),
    (0x5291F4, bytes.fromhex("81ff2a040000"), bytes.fromhex("81ffa8040000")),
    *detour(0x52913C, bytes.fromhex("6a1c 6a46 6a00 680d010000 57") + rel32(b"\xe8", 0x529148, 0x590350),
            CAVE + 0x200, 0x52914D, bytes.fromhex("6a1c 68b0000000 6a00 6826010000 57"), call=0x590350),

    *detour(0x52EA74, bytes.fromhex("8b442468 6a01 6a00 83c06d 50 53 56"), CAVE + 0x240, 0x52EA82,
            bytes.fromhex("69ddb6000000 035c2464 83c313 8b442468 6a01 6a00 0586000000 50 53 56")),
    *detour(0x52EBAB, bytes.fromhex("8b4c2468 6a01 6a00 83c16d 51 56 50"), CAVE + 0x280, 0x52EBB9,
            bytes.fromhex("be03000000 2bf3 69f6b6000000 03742464 83c613"
                          "8b4c2468 6a01 6a00 81c186000000 51 56 50")),

    *[(va, bytes.fromhex(stock), bytes.fromhex(wide)) for va, stock, wide in (
        (0x52FC4D, "81fbdd000000", "81fbd0000000"), (0x52FC55, "bbdd000000", "bbd0000000"),
        (0x52FD1E, "68dd000000", "68d0000000"), (0x52EEE0, "68dd000000", "68d0000000"),
        (0x52FA62, "68ca010000", "68d3010000"), (0x52ED51, "6882010000", "6898010000"))],

    *detour(0x52CC8C, bytes.fromhex("6a1c 6a62 056c010000"), CAVE + 0x740, 0x52CC95,
            bytes.fromhex("6a1c 6896000000 0576010000")),
    (0x52CCF2, bytes.fromhex("6bc94d"), bytes.fromhex("6bc95b")),
    *detour(0x52CD02, bytes.fromhex("6a1c 6a46 6a00 81c144030000 684a010000 51") + rel32(b"\xe8", 0x52CD14, 0x590350),
            CAVE + 0x2C0, 0x52CD19, bytes.fromhex("6a1c 68b0000000 6a00 03c9 81c187020000 6856010000 51"),
            call=0x590350),

    *detour(0x595612, bytes.fromhex("6880000000 6a7c 68d8010000"), CAVE + 0x540, 0x59561E,
            bytes.fromhex("6880000000 6896000000 6850020000")),
    *detour(0x59569E, bytes.fromhex("6880000000 6a7c 68d8010000"), CAVE + 0x560, 0x5956AA,
            bytes.fromhex("6880000000 6896000000 6850020000")),
    (0x59866D, bytes.fromhex("81c5e8000000"), bytes.fromhex("81c562ffffff")),
    (0x5986F4, bytes.fromhex("81c3e8000000"), bytes.fromhex("81c362ffffff")),
    *detour(0x5986D4, bytes.fromhex("8bd6 6bd24d 6a1c 6a46 57 8d4b6d 51 8d842ad0000000 50"), CAVE + 0x340,
            0x5986EA, bytes.fromhex("69d6b6000000 6a1c 68b0000000 57 8d8b86000000 51 8d842a13000000 50")),

    (0x5292F1, bytes.fromhex("688b010000"), bytes.fromhex("68a1010000")),
    (0x529270, bytes.fromhex("689b010000"), bytes.fromhex("68b1010000")),
    (0x529246, bytes.fromhex("68b7010000"), bytes.fromhex("68cd010000")),

    *[(va, bytes.fromhex("68" + stock), bytes.fromhex("68" + wide)) for va, stock, wide in (
        (0x598E08, "79010000", "63010000"), (0x598E1B, "79010000", "63010000"),
        (0x598E38, "5d010000", "47010000"), (0x598ED1, "8e010000", "78010000"),
        (0x598F01, "8e010000", "78010000"))],

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

    *widths(0x59506C, stock=350, wide=478),
    (0x595135, bytes.fromhex("8dbc3918010000"), bytes.fromhex("8dbc3998010000")),
    *widths(0x5951E6, stock=366, wide=494),
    *widths(0x526DE4, stock=378, wide=506),

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
    *widths(0x554387, stock=376, wide=504),

    *widths(0x587C52, 0x587C5C, stock=308, wide=600),
    *widths(0x542B30, 0x542B50, 0x542B5A, stock=308, wide=600),

    (0x542B5F, bytes.fromhex("6a30"), bytes.fromhex("6a00")),

    *detour(0x5432F9, bytes.fromhex("6826010000 8bf1 8b0dc075c200 52 56 55"), CAVE + 0x4E0, 0x543309,
            assembled(CAVE + 0x4E0,
                      bytes.fromhex("50 50 51 52"),
                      bytes.fromhex("6a05 6a18 6a18"), 0x54C350, bytes.fromhex("83c40c"),
                      bytes.fromhex("83ec40 57 8d442404 50"), 0x58CE50, bytes.fromhex("83c408"),
                      bytes.fromhex("6a00 6a00 50"), 0x54BF80, bytes.fromhex("83c44c"),
                      bytes.fromhex("2dc2000000 7e02 eb02 31c0"),
                      bytes.fromhex("29c5 0526010000 8944240c"),
                      bytes.fromhex("5a 59 58 8bf1 8b0dc075c200 52 56 55"))),

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

    (0x538C9C, bytes.fromhex("683e010000"), bytes.fromhex("68f6000000")),
    (0x538CA3, bytes.fromhex("689a000000"), bytes.fromhex("6852000000")),
    (0x538CB0, bytes.fromhex("685a030000"), bytes.fromhex("68a2030000")),

    *widths(0x53AB6C, 0x53AB77, 0x53ABAD, 0x53ABB8, stock=196, wide=312),

    *widths(0x53AB3E, 0x53AB45, stock=154, wide=196),

    *widths(0x53A631, stock=293, wide=125),
    *widths(0x53A62C, stock=424, wide=426),
    *widths(0x53A625, stock=708, wide=1044),
    *widths(0x53A620, stock=188, wide=197),
    *widths(0x53A663, stock=292, wide=124),
    *widths(0x53A65E, stock=426, wide=428),
    *widths(0x53A655, stock=182, wide=191),
    *widths(0x53A73D, 0x53A68B, stock=307, wide=139),
    *widths(0x539F94, stock=314, wide=146),
    *widths(0x53A686, stock=500, wide=529),
    *widths(0x53A699, stock=528, wide=557),
    *widths(0x53A7AC, stock=314, wide=146),
    *widths(0x53A7A7, stock=522, wide=551),
    *widths(0x53A7A0, stock=673, wide=1009),
    (0x53A79E, b"\x6a\x23", b"\x6a\x45"),
    (0x53A764, bytes.fromhex("83ff03"), bytes.fromhex("83ff02")),
    (0x710298, bytes.fromhex("ec000000 10010000 34010000"), bytes.fromhex("ed000000 21010000 21010000")),

    *widths(0x5A5971, 0x5A597C, 0x5A6946, 0x5A6951, 0x5A69F6, 0x5A6A01, 0x5A6AC6, 0x5A6AD1, 0x5A6B69, 0x5A6B74,
            0x5A6D1A, 0x5A6D25, 0x5A6F2B, 0x5A6F36, 0x5A7122, 0x5A712D, stock=238, wide=412),
    *widths(0x5A67A4, 0x5A67AF, 0x5A6814, 0x5A681F, stock=140, wide=162),
    (0x5A67F8, bytes.fromhex("81c3a1000000"), bytes.fromhex("81c3b7000000")),

    (0x5A66F3, bytes.fromhex("bb1a020000"), bytes.fromhex("bb26010000")),
    (0x5A6700, bytes.fromhex("681a020000"), bytes.fromhex("6826010000")),
    *widths(0x5A6783, stock=641, wide=906),

    *widths(0x5807AF, 0x5807BA, 0x58081D, 0x580828, stock=140, wide=162),
    (0x580804, bytes.fromhex("81c6a1000000"), bytes.fromhex("81c6b7000000")),

    (0x580780, bytes.fromhex("beb6010000"), bytes.fromhex("beab000000")),
    (0x58076A, bytes.fromhex("68b6010000"), bytes.fromhex("68ab000000")),
    *widths(0x580791, stock=641, wide=906),

    *widths(0x598395, stock=257, wide=312),
    *widths(0x5982D3, 0x59840D, stock=541, wide=596),
    *widths(0x598495, 0x5984E5, stock=497, wide=552),
    *widths(0x5984DD, stock=556, wide=501),
    *widths(0x59853F, stock=535, wide=590),
    *widths(0x598534, stock=507, wide=452),

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

    *widths(0x556D81, stock=215, wide=351), *widths(0x556E7A, stock=189, wide=325),
    (0x556D9F, bytes.fromhex("8d4e23"), bytes.fromhex("8d4e59")),
    (0x556DB4, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x556DB7, bytes.fromhex("8d4649"), bytes.fromhex("8d467f")),
    (0x556DD5, bytes.fromhex("8d869d000000"), bytes.fromhex("8d86ed000000")),
    (0x55742F, bytes.fromhex("69c9cf010000"), bytes.fromhex("69c901020000")),
    (0x557443, bytes.fromhex("8d9c0a29ffffff"), bytes.fromhex("8d9c0aa1feffff")),
    (0x557456, bytes.fromhex("bbf8000000"), bytes.fromhex("bba2000000")),
    (0x557506, bytes.fromhex("69c91c030000"), bytes.fromhex("69c9ea020000")),
    (0x557529, bytes.fromhex("c7442410e4010000"), bytes.fromhex("c744241016020000")),

    *widths(0x55B55E, stock=209, wide=345), *widths(0x55B656, stock=189, wide=325),
    (0x55B57B, bytes.fromhex("8d5723"), bytes.fromhex("8d5759")),
    (0x55B58C, bytes.fromhex("6a46"), bytes.fromhex("6a60")),
    (0x55B58F, bytes.fromhex("8d4749"), bytes.fromhex("8d477f")),
    (0x55B5AC, bytes.fromhex("8d979d000000"), bytes.fromhex("8d97ed000000")),
    (0x55BAF0, bytes.fromhex("69c9d7010000"), bytes.fromhex("69c912020000")),
    (0x55BB04, bytes.fromhex("8dbc021fffffff"), bytes.fromhex("8dbc0297feffff")),
    (0x55BB15, bytes.fromhex("bff6000000"), bytes.fromhex("bfa9000000")),
    (0x55BBE7, bytes.fromhex("69f60e030000"), bytes.fromhex("69f6d3020000")),
    (0x55BC0A, bytes.fromhex("c7442414f2010000"), bytes.fromhex("c74424142d020000")),
    *[(va, bytes.fromhex("6a46"), bytes.fromhex("6a60")) for va in (0x5574DF, 0x5574E7, 0x55BB9A, 0x55BBA0)],
    (0x5574F0, bytes.fromhex("83c349"), bytes.fromhex("83c37f")),
    (0x55BBA8, bytes.fromhex("83c749"), bytes.fromhex("83c77f")),

    *widths(0x552F9D, 0x552FA8, 0x552FCF, 0x552FDA, stock=196, wide=312),

    *widths(0x552E76, stock=212, wide=362), *widths(0x552F09, stock=189, wide=339),
    *widths(0x552F7F, stock=392, wide=472),
    (0x553139, bytes.fromhex("69c96c010000"), bytes.fromhex("69c949010000")),
    (0x55315E, bytes.fromhex("bfec000000"), bytes.fromhex("bfc9000000")),
    (0x5531BB, bytes.fromhex("69c9d4020000"), bytes.fromhex("69c9a1020000")),
    (0x5531DA, bytes.fromhex("bf2c020000"), bytes.fromhex("bf5f020000")),
    *detour(0x553011, bytes.fromhex("6a1c 6a70 6a00 6a00 6a1c 6a70"), CAVE + 0x100, 0x55301D,
            bytes.fromhex("6a1c 68a0000000 6a00 6a00 6a1c 68a0000000")),
    *detour(0x553035, bytes.fromhex("6a1c 6a70 6a00 6a00 6a1c 6a70"), CAVE + 0x140, 0x553041,
            bytes.fromhex("6a1c 68a0000000 6a00 6a00 6a1c 68a0000000")),

    (0x55BCF7, bytes.fromhex("e8d4fafeff"), bytes.fromhex("0f1f440000")),

    (0x595368, bytes.fromhex("e86364fbff"), bytes.fromhex("0f1f440000")),

    *widths(0x536654, stock=245, wide=443), *widths(0x536694, stock=147, wide=345),
    *widths(0x536828, stock=189, wide=353),
    (0x53665C, bytes.fromhex("c7442430f5000000"), bytes.fromhex("c7442430bb010000")),
    (0x5366FD, bytes.fromhex("b893000000"), bytes.fromhex("b859010000")),
    (0x536791, bytes.fromhex("8d8dcb000000"), bytes.fromhex("8d8d91010000")),
    (0x5368C4, bytes.fromhex("81c2d2000000"), bytes.fromhex("81c276010000")),
    (0x5368F3, bytes.fromhex("05d2000000"), bytes.fromhex("0576010000")),
    (0x536970, bytes.fromhex("69d25f010000"), bytes.fromhex("69d2db010000")),
    (0x536984, bytes.fromhex("8db4020bffffff"), bytes.fromhex("8db40245feffff")),
    (0x53699B, bytes.fromhex("be6a000000"), bytes.fromhex("be20000000")),

    *widths(0x535E5D, stock=294, wide=410),

    (0x5519EE, bytes.fromhex("c70594d0c2009a010000"), bytes.fromhex("c70594d0c2006a010000")),
    (0x55128E, bytes.fromhex("81c59a010000"), bytes.fromhex("81c56a010000")),
    (0x551284, bytes.fromhex("83e581"), bytes.fromhex("83e5b1")),

    (0x54EC48, bytes.fromhex("83c208"), bytes.fromhex("83c209")),
    (0x54EF45, bytes.fromhex("83c208"), bytes.fromhex("83c209")),

    *widths(0x59C421, 0x59C42C, stock=266, wide=366),
    (0x59C48E, bytes.fromhex("b80a010000"), bytes.fromhex("b86e010000")),
    (0x59C3DF, bytes.fromhex("bfe8000000"), bytes.fromhex("bfb6000000")),
    (0x59C3E6, bytes.fromhex("bf13030000"), bytes.fromhex("bfe1020000")),

    *widths(0x594EDA, stock=385, wide=473),

    *widths(0x52B59B, stock=448, wide=578), *widths(0x52B6B9, stock=431, wide=561),
    (0x52B715, bytes.fromhex("8d83d9000000"), bytes.fromhex("8d835b010000")),
    (0x52B75C, bytes.fromhex("81c3f7000000"), bytes.fromhex("81c379010000")),
    (0x52B817, bytes.fromhex("81c1ad010000"), bytes.fromhex("81c12f020000")),
    (0x52B8EA, bytes.fromhex("053e010000"), bytes.fromhex("05c0010000")),
    (0x52BAB7, bytes.fromhex("69c9af020000"), bytes.fromhex("69c9a2020000")),
    (0x52BACB, bytes.fromhex("8dbc0240feffff"), bytes.fromhex("8dbc02befdffff")),
    (0x52BADF, bytes.fromhex("bfef000000"), bytes.fromhex("bf60000000")),
    (0x52BB7D, bytes.fromhex("69c92d020000"), bytes.fromhex("69c93a020000")),
    (0x52BBFF, bytes.fromhex("69f62d020000"), bytes.fromhex("69f63a020000")),
    (0x52BB9C, bytes.fromhex("b8d3020000"), bytes.fromhex("b8c6020000")),
    (0x52BC2F, bytes.fromhex("b8d3020000"), bytes.fromhex("b8c6020000")),
    *widths(0x52B956, stock=385, wide=473),
    (0x52B9C3, bytes.fromhex("8d8503010000"), bytes.fromhex("8d855b010000")),
    (0x52BA67, bytes.fromhex("81c5ee000000"), bytes.fromhex("81c546010000")),

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

    (0x52E7B2, bytes.fromhex("8d8683000000"), bytes.fromhex("8d86af000000")),
    (0x52E83B, bytes.fromhex("8d9683000000"), bytes.fromhex("8d96af000000")),

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

    (0x5488E8, bytes.fromhex("c7442408c0000000"), bytes.fromhex("c744240864000000")),
    (0x5488F8, bytes.fromhex("c7442418f8000000"), bytes.fromhex("c74424189c000000")),
    (0x548900, bytes.fromhex("c744241c10030000"), bytes.fromhex("c744241cc8030000")),
    (0x548910, bytes.fromhex("c744242808040000"), bytes.fromhex("c744242864040000")),
    *[(va, bytes.fromhex("6a38"), bytes.fromhex("6a40")) for va in (0x5489FD, 0x548A06)],
    *widths(0x5489FF, 0x548A08, stock=294, wide=1024),
    *widths(0x548A13, stock=226, wide=134),
    *widths(0x548A0E, stock=412, wide=368),
    *widths(0x548980, stock=454, wide=404),
    *widths(0x548976, stock=178, wide=240),

    *widths(0x56108C, 0x59C71C, stock=756, wide=1229),
    *widths(0x561098, stock=262, wide=25),
    *widths(0x59C728, stock=232, wide=25),
    *widths(0x561087, stock=262, wide=305),

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
    *detour(0x570608, bytes.fromhex("896c241c c744242001000000"), CAVE + 0x4A0, 0x570614,
            bytes.fromhex("c744241c0e000000 c74424200f000000")),

    *widths(0x56CDAB, 0x56CDB5, stock=140, wide=200),
    (0x56CD60, bytes.fromhex("8daffe000000"), bytes.fromhex("8dafe0000000")),

    *widths(0x56BFD5, 0x56BFDF, stock=140, wide=220),
    *widths(0x56C185, 0x56C18F, 0x56C21D, 0x56C227, 0x56C2BB, 0x56C2C5,
            stock=140, wide=200),
    *((va, bytes.fromhex("81c318010000"), bytes.fromhex("81c3fa000000"))
      for va in (0x56C12A, 0x56C1DA, 0x56C26E)),
    (0x56BF86, bytes.fromhex("81c318010000"), bytes.fromhex("81c3f0000000")),
    (0x56C00D, bytes.fromhex("81c38c000000"), bytes.fromhex("81c396000000")),

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

    *widths(0x596839, stock=0x330, wide=992),
    *[(va, bytes.fromhex("68e7000000"), bytes.fromhex("6890000000")) for va in (0x596845, 0x596854, 0x596865, 0x596877)],
    (0x596890, bytes.fromhex("8d515d"), bytes.fromhex("8d5106")),
    (0x5968A1, bytes.fromhex("8d485d"), bytes.fromhex("8d4806")),

    (0x596666, bytes.fromhex("8b0d7cec7900"), rel32(b"\xe9", 0x596666, CAVE + 0x620) + b"\x90"),
    (CAVE + 0x620, b"\xcc" * 32, b"\x80\x3d" + p32(PAD_PROMPTS) + bytes.fromhex("00 750b 8b0d7cec7900")
     + rel32(b"\xe9", CAVE + 0x62F, 0x59666C) + bytes.fromhex("8b8c2484000000") + rel32(b"\xe9", CAVE + 0x63B, 0x5967D9)),

    *widths(0x595F17, stock=308, wide=627),
    (0x582619, bytes.fromhex("81c672020000"), bytes.fromhex("81c6d2010000")),
    (0x553885, bytes.fromhex("68e9010000"), bytes.fromhex("684a010000")),

    (0x598859, bytes.fromhex("81c234010000"), bytes.fromhex("81c22c010000")),

    (0x597512, bytes.fromhex("68b2010000"), bytes.fromhex("68d1010000")),
    *widths(0x597517, stock=532, wide=804 + ABILITY_CELL_GROWTH),
    (0x5975BB, bytes.fromhex("6805020000"), b"\x68" + p32(482 + ABILITY_CELL_GROWTH)),
    *widths(0x5975CF, 0x59761D, stock=452, wide=724),
    (0x597624, bytes.fromhex("6879010000"), bytes.fromhex("6899010000")),
    *[(va, bytes.fromhex("6886010000"), bytes.fromhex("68cc010000")) for va in (0x597675, 0x5976A2)],
    (0x59767A, bytes.fromhex("68d1010000"), bytes.fromhex("685d020000")),
    (0x5976A7, bytes.fromhex("68f1010000"), bytes.fromhex("685d020000")),
    (0x5976D1, bytes.fromhex("6886010000"), bytes.fromhex("68a5010000")),
    (0x596E9E, bytes.fromhex("81c151010000"), bytes.fromhex("81c171010000")),

    (0x598099, bytes.fromhex("83e05b"), bytes.fromhex("83e07b")),
    (0x5980A1, bytes.fromhex("05c4000000"), bytes.fromhex("05f4000000")),
    (0x59812F, bytes.fromhex("83c638"), bytes.fromhex("83c654")),
    (0x5981A3, bytes.fromhex("81c7d2000000"), bytes.fromhex("81c70a010000")),

    (0x598221, bytes.fromhex("81c6f5000000"), bytes.fromhex("81c66d010000")),
]

LATIN_TOKEN = 0x1000

def _fullwidth(byte: int) -> int:
    return {0x20: 0x8140, 0x22: 0x814A, 0x27: 0x8166}.get(byte) or int.from_bytes(
        chr(byte + 0xFEE0).encode("cp932"), "big")

FONT_ROUTE = [
    (0x549A49, bytes.fromhex("752a"), bytes.fromhex("9090")),
    (0x549A4B, bytes.fromhex("8b35bcba7900 8b0db8ba7900 33c0 85f6 7665 8a54240c 3a140874 61 83c001 3bf0 77f4"
                             " 5f b808000000 5e c3 3c02 7549"),
     bytes.fromhex("0fb744240c 2d00100000 83f85d 7707 058c0a0000 eb66 2d40740000 83f804 770e 05ea0a0000 eb55"
                   + " 90" * 7)),

    *[(0x79BA98 + 2 * b, struct.pack("<H", _fullwidth(b)), struct.pack("<H", LATIN_TOKEN + b - 0x20))
      for b in range(0x20, 0x7E) if b not in b"\\`{|}"],
]

def settings_rows(rows: int) -> list:
    count = 7 + rows
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

#!/usr/bin/env python3
"""Print donor text candidates beside the translation table, for review before rows are added or changed.

    uv run python tools/text_candidates.py coverage            # per stock text source: rows by source, keys without one
    uv run python tools/text_candidates.py wo2-psp [--all] [--rows] # WO2 PSP pairings (paired_psp_rows) against the table
    uv run python tools/text_candidates.py official [--all]     # WO1 and WO2 X360 pairings (official_pairs) against it
    uv run python tools/text_candidates.py prx WORDS            # WO2 PSP program strings holding WORDS, with their slot

Without --all, only the candidates that need a decision print: no row yet, our own row where the donor has English,
or a PSP row that points at other English. --rows prints those PSP candidates as table rows to paste after review.
Donor priority for text is WO1 X360, then WO2 X360, then WO2 PSP, so a PSP candidate never replaces an Xbox 360 row.
The stock game and donor folder are WOZMOD_GAME and WOZMOD_DONORS from `[tests]` in work/harness.toml, or --game and
--donors. The output quotes game text: keep it in the terminal, never in the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import checkout
from wozmod.text.inventory import JAPANESE, inventory
from wozmod.text.resolver import DonorResolver
from wozmod.text.stock import stock_key

REPO = Path(__file__).resolve().parents[1]
TABLE = REPO / "data/text/table.json"
LEFT_JAPANESE = REPO / "data/text/left_japanese.json"
XBOX = ("wo1-x360", "wo2-x360")
REVIEW = ("new", "ours", "differs", "xbox-unused")


def review_status(row: dict | None, ref: list | None, english: str, shown: str | None, donor: str) -> str:
    """How a donor candidate stands against the table row for its stock string (shown is the row's English).

    new: no row; same: the row already says it; ours: an authored or edited row where the donor has English; xbox: an
    Xbox 360 row outranks this PSP candidate; differs: a donor row with other English; xbox-unused: a PSP row where
    the Xbox 360 candidate (which outranks it) exists.
    """
    if row is None:
        return "new"
    source = row["source"]
    if (ref is not None and row.get("ref") == ref) or shown == english:
        return "same"
    if source in ("authored", "edited"):
        return "ours"
    if donor == "wo2-psp" and source in XBOX:
        return "xbox"
    return "xbox-unused" if source == "wo2-psp" and donor in XBOX else "differs"


def _inputs(args) -> tuple[Path, Path]:
    tests = checkout.inputs()
    game, donors = args.game or tests.get("WOZMOD_GAME"), args.donors or tests.get("WOZMOD_DONORS")
    if not game or not donors:
        sys.exit("name the stock game and donor folder: --game and --donors, or [tests] in work/harness.toml")
    return Path(game), Path(donors)


def _rows() -> dict[str, dict]:
    return {row["key"]: row for row in json.loads(TABLE.read_text(encoding="utf-8"))["rows"]}


def _left_japanese() -> dict[str, str]:
    return {entry["key"]: entry["reason"] for entry in json.loads(LEFT_JAPANESE.read_text(encoding="utf-8"))["keys"]}


def _jp(text: bytes) -> str:
    return text.decode("cp932", "replace").replace("\n", " | ")


def coverage(args) -> None:
    game, _ = _inputs(args)
    rows, allowed = _rows(), _left_japanese()
    for source, texts in inventory(game).items():
        sources = Counter(rows[key]["source"] if key in rows else "left Japanese" if key in allowed else "NO ROW"
                          for key in (stock_key(text)[0] for text in texts))
        print(f"{source:16} {len(texts):6} strings  " + "  ".join(f"{s} {n}" for s, n in sorted(sources.items())))
        for text in texts:
            if (key := stock_key(text)[0]) not in rows and key not in allowed:
                print(f"    NO ROW {key[:12]}  {_jp(text)[:70]}")


def _print(status: str, key: str, where, ref, stock: bytes, english: str, row: dict | None, shown: str | None) -> None:
    print(f"{status:11} {key[:12]}  {where}  {json.dumps(ref) if ref else ''}")
    print(f"    stock : {_jp(stock)[:100]}")
    print(f"    donor : {english[:100]}")
    if row is not None:
        print(f"    table : {(shown or '')[:100]}  ({row['source']} {json.dumps(row.get('ref')) if 'ref' in row else ''})")


def wo2_psp(args) -> None:
    from wozmod.donors import DonorsFolder
    from wozmod.text.psp_alignment import paired_psp_rows

    game, donors = _inputs(args)
    resolver, rows = DonorResolver(donors), _rows()
    fns = DonorsFolder(donors).folder("wo2-psp") / "PSP_GAME/USRDIR/LINKDATA_NA.FNS"
    counts, pasted = Counter(), {}
    for pair in paired_psp_rows(game, fns):
        if not (JAPANESE.search(pair.stock) and pair.stock.strip(b" \n\x81\x40") and (found := stock_key(pair.stock))):
            continue  # a blank stock line: a row for it would put this English on every blank line
        key, row, english = found[0], rows.get(found[0]), resolver.resolve(pair.ref)
        shown = resolver.resolve_row(row) if row else None
        status = review_status(row, pair.ref, english, shown, "wo2-psp")
        counts[(pair.group, status)] += 1
        if args.all or status in REVIEW:
            _print(status, key, pair.location, pair.ref, pair.stock, english, row, shown)
        if status in ("new", "ours") and key not in pasted:
            pasted[key] = {"key": key, "source": "wo2-psp", "ref": pair.ref}
    for (group, status), count in sorted(counts.items()):
        print(f"{count:6}  {group}: {status}")
    if args.rows:
        print(json.dumps(list(pasted.values()), indent=2))


def official(args) -> None:
    from wozmod.donors import DonorsFolder
    from wozmod.text.script_alignment import official_pairs, wo2_x360

    game, donors = _inputs(args)
    folders, resolver, rows = DonorsFolder(donors), DonorResolver(donors), _rows()
    pairs = official_pairs(game, folders.folder("wo1-x360") / "linkdata_ens", wo2_x360(folders.folder("wo2-x360") / "linkdata_ens"))
    counts = Counter()
    for stock, (english, source) in pairs.items():
        if not (found := stock_key(stock)):
            continue
        key, row = found[0], rows.get(found[0])
        shown = resolver.resolve_row(row) if row else None
        status = review_status(row, None, english, shown, source)
        counts[(source, status)] += 1
        if args.all or status in REVIEW:
            _print(status, key, source, None, stock, english, row, shown)
    for (source, status), count in sorted(counts.items()):
        print(f"{count:6}  {source}: {status}")


def prx(args) -> None:
    _, donors = _inputs(args)
    words = " ".join(args.words).casefold()
    for slot, text in enumerate(DonorResolver(donors).psp_pool()):
        if text and words in text.casefold():
            print(f'["wo2-psp", "prx", {slot}]  {text[:100]!r}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--game", help="the stock game folder")
    parser.add_argument("--donors", help="the extracted donor folder")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("coverage", help="rows per stock text source, and the keys without one")
    for name in ("wo2-psp", "official"):
        command = commands.add_parser(name, help=f"{name} candidates against the table")
        command.add_argument("--all", action="store_true", help="also print the candidates the table already settles")
    commands.choices["wo2-psp"].add_argument("--rows", action="store_true", help="print the new and ours candidates as rows")
    commands.add_parser("prx", help="search the PSP program strings").add_argument("words", nargs="+")
    args = parser.parse_args(argv)
    {"coverage": coverage, "wo2-psp": wo2_psp, "official": official, "prx": prx}[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

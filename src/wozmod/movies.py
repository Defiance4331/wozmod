"""English movies: donor movie files copied as they are into wozmod/movie/<setting>/movieNNN.wmv.

The runtime plays wozmod/movie/voice/movieNNN.wmv in place of movie/movieNNN.wmv while the voices are English,
and wozmod/movie/text/movieNNN.wmv (title cards) while the text is English.
"""

from __future__ import annotations

import json
from pathlib import Path

from wozmod import resources
from wozmod.donors import DONORS, Donor, DonorFile

DATA = "data/movies.json"  # under resources.root(), read at call time: a frozen bundle's root is not the import's
LANGUAGES = ("voice", "text")


def movie_donors(data: Path | None = None) -> dict[str, Donor]:
    """One extraction per donor disc, its members named by where the game folder gets them under wozmod/movie."""
    rows = json.loads((data or resources.root() / DATA).read_text(encoding="utf-8"))["movies"]
    by_donor: dict[str, list[DonorFile]] = {}
    for row in rows:
        if row["language"] not in LANGUAGES:
            raise ValueError(f"{row['slot']}: unknown language setting {row['language']!r}")
        by_donor.setdefault(row["donor"], []).append(
            DonorFile(f"{row['language']}/{row['slot']}.wmv", row["source"], row["size"], row["sha256"]))
    return {name: Donor(name, DONORS[name].format, tuple(files))
            for name, files in by_donor.items()}

"""Pair a stock stage script's events with its donor script's by moment (decisions ledger text row 49).

A moment is a stock event, or a chain of stock events MOZ split from one donor event, matched to the donor event with
the same trigger, the same gameplay commands, notices and speakers. A stage's lines then pair through their moments:
the n-th speak of a stock event with the n-th speak of its donor event (script_alignment.stage_join). Order across the
script never pairs anything: MOZ inserts, drops, moves, splits and copies events, so the script's order drifts (Dream
stage 48 holds the messenger's success and failure scenes in the other order from WO2 X360's, decisions ledger text
row 49).

Same trigger. Each event's conditions are a tree (formats.scripts._event_scripts) whose leaves are tests: a type and
eight words. Two triggers are alike (score, 0 to 1) leaf for leaf, after these mappings, each needed on a real stage
(work/handbacks/event-matcher-verify.md, section B):
- A team leaf (type 0x23: word 1 an officer, word 2 whether the officer must be on the player's team) is a constant
  under a fixed team, and the tree folds (rule 2 below). Without a team it stays a leaf.
- An event reference (type 0x11: word 0 an event, word 1 whether it has run, word 2 whether the game tests it again
  when the event is dequeued) compares through the matching itself, which repeats until it no longer changes. A
  reference to a stock event with no match yet compares by that event's own trigger and commands, two levels deep, or
  is unknown, neither for nor against (Dream stage 60's events 14 and 15 reference 13, MOZ's silent half of WO2's 11).
- A unit (placement index) in a command or condition word (UNIT_COMMAND_WORDS, UNIT_CONDITION_WORDS) maps through the
  two placement tables aligned by cast (unit_labels).
- WO1 X360 numbers condition types 0x0A-0x0E one higher and 0x10-0x23 one lower than MOZ (WO1_TYPES).
- A condition MOZ adds (a not-run guard, a unit-alive test) costs less than one the donor has and MOZ lacks (Dream
  stage 58, events 33 and 34).
- MOZ rejoins a split trigger as "event A or event B has run" (Dream stages 52 and 60): the best branch compares.
- MOZ moves a trigger's test into a silent event of its own and gates the speak on it (Dream stage 64, events 59 and
  60): the silent event's trigger stands in for the reference (_inlined).

Dream stages 40-67. WO2 X360 plays each Dream stage with a fixed team (script_alignment.dream_teams) and its scripts
test no team; MOZ lets the player pick and gates events on the team. Under WO2's team a stock event whose gate fails
cannot run: it is a variant, and matches nothing (Dream stage 60 event 19, spoken for teams without Ling Tong where
WO2's team hears event 20). MOZ's fallbacks, events gated on an earlier event not having run, need no rule here: on
the real scripts none that WO2's team closes would match a donor event. The story and Z Dream donors test the team
themselves, so their stages have no fixed team.

Chains. MOZ splits one donor event into a head with the donor's trigger (spoken or silent: the gameplay, a notice)
and events gated on a chain member having run, whose speakers concatenate to the donor's (Dream stage 48: WO2 event
68 is MOZ 79 and 80; 40: WO2 27 is MOZ 38, 39, 40, 41). Identical twins, events with the same trigger and commands
that speak different lines (story stage 8, WO2 events 28 and 33), pair in index order within their group: nothing
else tells them apart.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

from wozmod.formats.scripts import NOTICE, SPEAK, Script

AND, OR, RAN, TEAM = 0xFF, 0xFE, 0x11, 0x23
# MOZ's condition type for each WO1 X360 one that differs.
WO1_TYPES = {**{t: t - 1 for t in range(0xA, 0xF)}, **{t: t + 1 for t in range(0x10, 0x24)}}
# The word that holds a unit (placement index): per command opcode, the word after the opcode's; per condition type
# (MOZ numbering), the word index. Learned on the stages MOZ renumbers its placements in (40-42, 48, 66).
UNIT_COMMAND_WORDS = {0x6: 2, 0x7: 2, 0x8: 2, 0x9: 2, 0xA: 1, 0xF: 3, 0x12: 1, 0x13: 2, 0x14: 2, 0x15: 1, 0x25: 2,
                      0x28: 2, 0x29: 2, 0x2A: 2, 0x2D: 2, 0x2E: 1}
UNIT_CONDITION_WORDS = {0x6: 0, 0x12: 3, 0x13: 0, 0x14: 1, 0x15: 1, 0x17: 1, 0x18: 1}
# A score at least this high makes two events (or two triggers) the same; below it they are different.
ALIKE = 0.6
# How many times the matching is scored again with the references the last round resolved.
ROUNDS = 4

# A condition tree: ("leaf", type, words), ("and" | "or", children), TRUE or FALSE. An event reference's word 0 is
# ("event", index), so comparisons can tell it from a plain word.
Node = tuple
TRUE, FALSE = ("true",), ("false",)


def condition_tree(conditions: tuple[tuple[int, tuple[int, ...]], ...]) -> Node:
    """An event's conditions (prefix order, formats.scripts._event_scripts) as a tree; no conditions is all of none."""
    def node(i: int) -> tuple[Node, int]:
        kind, words = conditions[i]
        if kind in (AND, OR):
            children, j = [], i + 1
            for _ in range(words[0]):
                child, j = node(j)
                children.append(child)
            return ("and" if kind == AND else "or", children), j
        return ("leaf", kind, words), i + 1
    return node(0)[0] if conditions else ("and", [])


def _folded(kind: str, children: list[Node]) -> Node:
    """A group of kind over children, flattened, with its constants folded."""
    flat = []
    for child in children:
        flat += child[1] if child[0] == kind else [child]
    absorb, unit = (FALSE, TRUE) if kind == "and" else (TRUE, FALSE)
    if absorb in flat:
        return absorb
    flat = [child for child in flat if child != unit]
    if not flat:
        return unit
    return flat[0] if len(flat) == 1 else (kind, tuple(flat))


def normal(node: Node, team: Callable[[int], bool] | None, wo1: bool, units: dict[int, int]) -> Node:
    """A raw tree simplified: WO1 types renumbered, unit words relabelled (units), team leaves constant under a team
    (team gives whether an officer is on it), event references marked, groups flattened and constants folded."""
    if node[0] == "leaf":
        kind, words = node[1], node[2]
        if wo1:
            kind = WO1_TYPES.get(kind, kind)
        if kind in UNIT_CONDITION_WORDS and words[UNIT_CONDITION_WORDS[kind]] in units:
            words = list(words)
            words[UNIT_CONDITION_WORDS[kind]] = units[words[UNIT_CONDITION_WORDS[kind]]]
        if kind == TEAM and team is not None:
            return TRUE if team(words[1]) == bool(words[2]) else FALSE
        if kind == RAN:
            return ("leaf", kind, (("event", words[0]), *words[1:]))
        return ("leaf", kind, tuple(words))
    return _folded(node[0], [normal(child, team, wo1, units) for child in node[1]])


def _refolded(node: Node) -> Node:
    if node[0] in ("leaf", "true", "false"):
        return node
    return _folded(node[0], [_refolded(child) for child in node[1]])


def _leaves(node: Node) -> list[Node]:
    if node[0] == "leaf":
        return [node]
    return [] if node[0] in ("true", "false") else [leaf for child in node[1] for leaf in _leaves(child)]


def _ran(node: Node) -> int | None:
    """The event a reference leaf names, else None."""
    return node[2][0][1] if node[0] == "leaf" and node[1] == RAN else None


def _without_ran(node: Node, chain: set[int]) -> Node:
    """node with each "event k has run" for k in chain made true: what is left of a chain piece's gate."""
    if node[0] == "leaf":
        return TRUE if _ran(node) in chain and node[2][1] == 1 else node
    if node[0] in ("true", "false"):
        return node
    children = [_without_ran(child, chain) for child in node[1]]
    if node[0] == "or" and TRUE in children:
        return TRUE
    children = [child for child in children if child != TRUE]
    if not children:
        return TRUE
    return children[0] if len(children) == 1 else (node[0], tuple(children))


@dataclass
class Event:
    """One event of a stage script as the matching sees it: its normal trigger (cond), whether it can run (live), its
    commands as tokens (a speak by its speaker, a notice by its words 3 and 4, others whole with units relabelled),
    its speaks as (speaker, line), its notices as donor notices (None: unknown) and its speakers."""

    index: int
    cond: Node
    tokens: tuple
    speaks: tuple[tuple[int, int], ...]
    notices: tuple
    speakers: tuple
    live: bool


def events(script: Script, speaker: Callable[[int], object], notice: Callable[[int], object],
           team: Callable[[int], bool] | None, wo1: bool, units: dict[int, int]) -> list[Event]:
    found = []
    for i, commands in enumerate(script.events):
        conditions = script.conditions[i] if i < len(script.conditions) else ()
        cond = normal(condition_tree(conditions), team, wo1, units)
        tokens, speaks, notices = [], [], []
        for c in commands:
            if c[0] == SPEAK:
                tokens.append((SPEAK, speaker(c[1])))
                speaks.append((c[1], c[2]))
            elif c[0] == NOTICE:
                tokens.append((NOTICE, c[3], c[4]))
                notices.append(notice(c[1]))
            else:
                if c[0] in UNIT_COMMAND_WORDS and c[UNIT_COMMAND_WORDS[c[0]]] in units:
                    c = list(c)
                    c[UNIT_COMMAND_WORDS[c[0]]] = units[c[UNIT_COMMAND_WORDS[c[0]]]]
                tokens.append(tuple(c))
        found.append(Event(i, cond, tuple(tokens), tuple(speaks), tuple(notices),
                           tuple(t[1] for t in tokens if t[0] == SPEAK), cond != FALSE))
    return found


def unit_labels(stock: Script, donor: Script, persons: dict[int, int]) -> tuple[dict[int, int], dict[int, int]]:
    """(stock unit to label, donor unit to label): the placement tables aligned by cast (persons maps the stock
    casts), runs of one cast as runs. A donor unit's label is its index, a merged run's its first index (MOZ merges WO2
    Dream stage 66's two Gracia placements 0 and 1 into its 0); a stock unit with no counterpart has none. Ids past
    the table (the player's own units: 0x61 in Dream stage 48, 0x66 and 0x67 in 66) keep their distance from the
    table's end [inference from stage 66, where MOZ's table is one shorter and its ids one lower]."""
    def runs(casts) -> list[tuple[object, list[int]]]:
        found: list[tuple[object, list[int]]] = []
        for i, cast in enumerate(casts):
            if found and found[-1][0] == cast:
                found[-1][1].append(i)
            else:
                found.append((cast, [i]))
        return found

    ours, theirs = runs([persons.get(c, ("stock", c)) for c in stock.casts]), runs(donor.casts)
    mine, donors = {}, {u: u for u in range(len(donor.casts))}
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, [c for c, _ in ours], [c for c, _ in theirs],
                                               autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for (_, a), (_, b) in zip(ours[i1:i2], theirs[j1:j2], strict=True):
                if len(a) == len(b):
                    mine.update(zip(a, b, strict=True))
                else:
                    donors.update((u, b[0]) for u in b)
                    mine.update((u, b[0]) for u in a)
    for v in range(len(stock.casts), len(stock.casts) + 64):
        mine[v] = v - len(stock.casts) + len(donor.casts)
    return mine, {u: label for u, label in donors.items() if u != label}


def _subsequence(a: tuple, b: tuple) -> bool:
    it = iter(b)
    return all(any(x is None or y is None or x == y for y in it) for x in a)


def _notices_agree(a: tuple, b: tuple) -> bool:
    """Equal notices (an unknown one is a wildcard), or one side's a subsequence of the other's: MOZ moves a speak
    event's notice into an event of its own."""
    return _subsequence(a, b) if len(a) <= len(b) else _subsequence(b, a)


def _command_likeness(a: Event, b: Event) -> float:
    return SequenceMatcher(None, a.tokens, b.tokens, autojunk=False).ratio() if a.tokens or b.tokens else 1.0


class StageMoments:
    """The moments of one stock stage script against its donor script.

    matches: stock event to the donor event it is, a whole moment. chains: donor event to its chain's pieces, each
    (stock event, offset of its first speaker in the donor event's speakers)."""

    def __init__(self, stock: Script, donor: Script, persons: dict[int, int], notices: dict[int, int],
                 team: frozenset[int] | None, wo1: bool) -> None:
        mine, donors = unit_labels(stock, donor, persons)
        on_team = None if team is None else (lambda officer: persons.get(officer, ("stock", officer)) in team)
        self.stock = events(stock, lambda p: persons.get(p, ("stock", p)), notices.get, on_team, False, mine)
        self.donor = events(donor, lambda p: p, lambda r: r, None, wo1, donors)
        self._depth = 0
        by_speakers = defaultdict(list)
        for d in self.donor:
            by_speakers[d.speakers].append(d)
        self.candidates: dict[tuple[int, int], float] = {}
        for m in self.stock:
            if not m.live:
                continue
            for d in by_speakers.get(m.speakers, []):
                if _notices_agree(m.notices, d.notices) and (m.speakers or set(m.tokens) & set(d.tokens)
                                                             or not m.tokens):
                    self.candidates[m.index, d.index] = _command_likeness(m, d)
        self.best: dict[int, int] | None = None
        self.refs: dict[int, int] = {}
        for _ in range(ROUNDS):
            self._score(self.best)
            best = self._best()
            if best == self.best:
                break
            self.best = best
        self.best = self.best or {}
        self.refs = dict(self.best)
        self._solve()

    # Likeness of triggers.
    def _tree(self, a: Node, b: Node, refs: dict[int, int] | None) -> float:
        """Likeness of a stock tree a and a donor tree b in [0, 1]; references compare through refs (None: any)."""
        if a[0] in ("true", "false") or b[0] in ("true", "false"):
            return 1.0 if a == b else 0.0
        if a[0] == "leaf" and b[0] == "leaf":
            return self._leaf(a, b, refs)
        kind = a[0] if a[0] != "leaf" else b[0]
        if a[0] != "leaf" and b[0] != "leaf" and a[0] != b[0]:
            kind = "and"
        ours = a[1] if a[0] == kind else (a,)
        theirs = b[1] if b[0] == kind else (b,)
        used, total = set(), 0.0
        for x in ours:
            best, at = 0.0, None
            for j, y in enumerate(theirs):
                if j not in used and (s := self._tree(x, y, refs)) > best:
                    best, at = s, j
            if at is not None:
                used.add(at)
                total += best
        if kind == "and":  # a conjunct MOZ adds costs less than a donor conjunct MOZ lacks
            return total / len(theirs) * 0.8 ** max(0, len(ours) - len(used))
        return total / max(len(ours), len(theirs))

    def _leaf(self, a: Node, b: Node, refs: dict[int, int] | None) -> float:
        if a[1] != b[1]:
            return 0.0
        same, refs_agree, unknown = 0.0, True, False
        for x, y in zip(a[2], b[2], strict=True):
            if not isinstance(x, tuple):
                same += x == y
            elif refs is not None and x[1] not in refs:
                deep = self._unmatched_reference(x[1], y[1], refs)
                if deep is None:  # the referenced stock event has no match (yet): neither for nor against
                    unknown = True
                    same += 1
                else:
                    refs_agree &= deep
                    same += deep
            else:
                good = refs is None or refs.get(x[1]) == y[1]
                refs_agree &= good
                same += good
        if not refs_agree:
            return 0.0
        if unknown:
            return 0.5 if same == len(a[2]) else 0.3
        return 1.0 if same == len(a[2]) else 0.5 + 0.5 * same / len(a[2]) - 0.1

    def _unmatched_reference(self, i: int, j: int, refs: dict[int, int]) -> bool | None:
        """A referenced stock event with no match against the donor's: True when their triggers and commands are
        alike (two levels deep), else None (unknown: MOZ often restructures the referenced event)."""
        if self._depth >= 2 or i >= len(self.stock) or j >= len(self.donor) or not self.stock[i].live:
            return None
        m, d = self.stock[i], self.donor[j]
        self._depth += 1
        try:
            return True if self._trigger(m.cond, d.cond, refs) * (0.7 + 0.3 * _command_likeness(m, d)) >= ALIKE \
                else None
        finally:
            self._depth -= 1

    def _trigger(self, a: Node, b: Node, refs: dict[int, int] | None) -> float:
        """_tree, or for a stock trigger that is an OR (MOZ rejoins its branches), the best of its branches."""
        s = self._tree(a, b, refs)
        if a[0] == "or":
            s = max([s] + [self._tree(k, b, refs) for k in a[1]])
        return s

    def _within(self, piece: Node, whole: Node, refs: dict[int, int] | None) -> float:
        """1 when every leaf of piece is a leaf of whole, else the trees' likeness."""
        leaves = _leaves(whole)
        if all(any(self._tree(x, y, refs) == 1.0 for y in leaves) for x in _leaves(piece)):
            return 1.0
        return self._tree(piece, whole, refs)

    def _inlined(self, cond: Node, refs: dict[int, int], depth: int) -> Node:
        """cond with each "stock event k has run", k live, silent and unmatched, replaced by k's trigger: MOZ moves a
        donor trigger's test into a silent event of its own and gates the speak on it."""
        if depth == 0 or cond[0] in ("true", "false"):
            return cond
        if cond[0] == "leaf":
            k = _ran(cond)
            if k is not None and cond[2][1] == 1 and k < len(self.stock) and k not in refs \
                    and self.stock[k].live and not self.stock[k].speakers:
                return self._inlined(self.stock[k].cond, refs, depth - 1)
            return cond
        return _refolded((cond[0], tuple(self._inlined(c, refs, depth) for c in cond[1])))

    # The matching.
    def _score(self, refs: dict[int, int] | None) -> None:
        self._depth = 0
        inlined = {m.index: self._inlined(m.cond, refs or {}, 2) for m in self.stock if m.live}
        self.score: dict[tuple[int, int], float] = {}
        for (i, j), commands in self.candidates.items():
            t = self._trigger(self.stock[i].cond, self.donor[j].cond, refs)
            e = inlined.get(i)
            if e is not None and e != self.stock[i].cond and any(_ran(x) is None for x in _leaves(e)):
                t = max(t, 0.95 * self._within(e, self.donor[j].cond, refs))
            self.score[i, j] = t * (0.7 + 0.3 * commands)

    def _ranked(self, i: int) -> list[tuple[float, int]]:
        return sorted(((s, j) for (a, j), s in self.score.items() if a == i), reverse=True)

    def _best(self) -> dict[int, int]:
        """Each stock event's donor event: its best score, if alike and not tied."""
        by: dict[int, list[tuple[float, int]]] = defaultdict(list)
        for (i, j), s in self.score.items():
            by[i].append((s, j))
        found = {}
        for i, ranked in by.items():
            ranked.sort(reverse=True)
            if ranked[0][0] >= ALIKE and (len(ranked) == 1 or ranked[1][0] < ranked[0][0]):
                found[i] = ranked[0][1]
        return found

    def _assign(self) -> dict[int, int]:
        """Stock event to donor event: of the stock events whose best is a donor event, the highest score (a tie:
        none)."""
        chose: dict[int, list[tuple[float, int]]] = defaultdict(list)
        for i, j in self.best.items():
            chose[j].append((self.score[i, j], i))
        found = {}
        for j, ranked in chose.items():
            ranked.sort(reverse=True)
            if len(ranked) == 1 or ranked[0][0] != ranked[1][0]:
                found[ranked[0][1]] = j
        return found

    def _chains(self, matches: dict[int, int]) -> dict[int, list[tuple[int, int]]]:
        """Donor voiced events with no whole match that a chain of stock events covers: a head with the donor's
        trigger, then live stock events gated on a chain member having run (the rest of their gate within the
        donor's) or sharing the trigger, whose speakers concatenate to the donor's in order, and silent links (MOZ
        gates a speak on a notice event that ran on the trigger)."""
        taken, matched, refs = set(matches), set(matches.values()), self.refs
        found = {}
        for d in self.donor:
            if not d.speakers or d.index in matched:
                continue
            best = None
            for h in self.stock:
                if not h.live or h.index in taken or (h.speakers and len(h.speakers) >= len(d.speakers)):
                    continue
                if d.speakers[:len(h.speakers)] != h.speakers:
                    continue
                if (head := self._trigger(h.cond, d.cond, refs)) < ALIKE:
                    continue
                chain, at = [(h, 0)], len(h.speakers)

                def follows(m: Event, ids: set[int]) -> bool:
                    rest = _without_ran(m.cond, ids)
                    return rest != m.cond and self._within(rest, d.cond, refs) >= ALIKE

                while at < len(d.speakers):
                    ids = {x.index for x, _ in chain}
                    grew = True
                    while grew:
                        grew = False
                        for m in self.stock:
                            if m.live and not m.speakers and m.index not in ids and m.index not in taken \
                                    and self.best.get(m.index) is None and follows(m, ids):
                                chain.append((m, at))
                                ids.add(m.index)
                                grew = True
                    piece = next((m for m in self.stock if m.live and m.speakers and m.index not in taken
                                  and m.index not in ids and d.speakers[at:at + len(m.speakers)] == m.speakers
                                  and (follows(m, ids) or max(self._trigger(m.cond, d.cond, refs),
                                                              self._within(m.cond, d.cond, refs)) >= ALIKE)), None)
                    if piece is None:
                        break
                    chain.append((piece, at))
                    at += len(piece.speakers)
                if at == len(d.speakers) and (best is None or head > best[0]):
                    best = (head, chain)
            if best:
                found[d.index] = [(m.index, at) for m, at in best[1]]
                taken |= {m.index for m, _ in best[1]}
        return found

    def _gameplay_pieces(self, refs: dict[int, int]) -> dict[int, int]:
        """Live silent stock events with no match whose commands are all commands of one voiced donor event with a
        like trigger (the gameplay half MOZ split off a speaking event): event to that donor event, for references."""
        found = {}
        for m in self.stock:
            if not m.live or m.speakers or m.index in refs or not m.tokens:
                continue
            tokens = Counter(m.tokens)
            ranked = sorted(((s, d.index) for d in self.donor if d.speakers and not tokens - Counter(d.tokens)
                             if (s := self._trigger(m.cond, d.cond, refs)) >= ALIKE), reverse=True)
            if ranked and (len(ranked) == 1 or ranked[1][0] < ranked[0][0]):
                found[m.index] = ranked[0][1]
        return found

    def _twins(self) -> None:
        """Identical events: unmatched live stock events whose best candidates tie on the same donor events, as many
        on each side, pair in index order."""
        groups: dict[tuple, list[int]] = defaultdict(list)
        for m in self.stock:
            if m.index in self.best or not m.live:
                continue
            ranked = self._ranked(m.index)
            if len(ranked) > 1 and ranked[0][0] >= ALIKE and ranked[0][0] == ranked[1][0]:
                groups[tuple(sorted(j for s, j in ranked if s == ranked[0][0])), ranked[0][0]].append(m.index)
        taken = set(self.best.values())
        for (tied, _), group in groups.items():
            if len(group) == len(tied) and not taken & set(tied):
                self.best.update(zip(sorted(group), tied, strict=True))

    def _solve(self) -> None:
        """Whole matches, then chains; the references then also see each chain's pieces and each split-off gameplay
        event as their donor event, and both are found again until nothing changes."""
        last = None
        for _ in range(ROUNDS):
            refs = dict(self.best)
            for j, pieces in self._chains(self._assign()).items():
                refs.update((i, j) for i, _ in pieces)
            refs |= self._gameplay_pieces(refs)
            if refs == last:
                break
            last = refs
            self._score(refs)
            self.best = self._best()
            self.refs = {**refs, **self.best}
        self._twins()
        self.matches = self._assign()
        self.chains = self._chains(self.matches)

    def line_moments(self) -> dict[int, set[int]]:
        """Each stock line a moment speaks, with the donor lines its moments speak there: the n-th speak of a matched
        stock event (or of a chain's pieces, from its offset) with the n-th of the donor event's."""
        found: dict[int, set[int]] = defaultdict(set)
        for i, j in self.matches.items():
            for (_, ours), (_, theirs) in zip(self.stock[i].speaks, self.donor[j].speaks, strict=True):
                found[ours].add(theirs)
        for j, pieces in self.chains.items():
            for i, at in pieces:
                for k, (_, ours) in enumerate(self.stock[i].speaks):
                    found[ours].add(self.donor[j].speaks[at + k][1])
        return dict(found)

"""A session's montages, derived from wl.works' probe positions and each run's channel selection (spec sections
4.1-4.3). Pure: no database, no I/O.

A montage is one position and one channel selection per probe, session-wide. Each position's two edges are placed in
gaps between runs (`edges.place`); the montages are cut at the gaps where a probe changes position (a `moved` end, or
the start of a serial's position that is not its first) and where a probe's selection changes (D6). A first insertion
and a last withdrawal split nothing (wl.works' P11).

Gaps are indexed 0..n as in `edges`. A position placed from gap `s` to gap `e` covers the runs at indices `s..e-1`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from wl_preproc.montage.edges import RunSpan, gap_name, place, widen_for, window

# wl.works' P10: a montage whose last run has zero length still holds that run's start.
ZERO_LENGTH_NUDGE_S = 0.001


@dataclass(frozen=True)
class PositionIn:
    """One wl.works position, its times naive UTC."""

    serial: str
    insertion_number: int
    inserted_at: datetime
    ended_at: datetime | None
    end_kind: str | None
    inserted_after_run: int | None
    ended_after_run: int | None


@dataclass(frozen=True)
class RunSelection:
    """A run's selection for one serial: the `electrode_config_hash` of the first and the last SpikeGLX segment that
    overlaps the run and places that serial's sites (D6), and whether the selection changed inside the run."""

    first_hash: str
    last_hash: str
    changed: bool  # any two of the run's segment hashes for the serial differ: A -> B -> A counts (D6)


@dataclass(frozen=True)
class DerivationInput:
    has_ephys: bool
    runs: tuple[RunSpan, ...]  # runs that do not end before they start, in start order
    positions: tuple[PositionIn, ...] | None  # None when none are stored
    positions_seq: int | None
    t0: datetime | None
    t0_source: str | None
    census_complete: bool
    selections: Mapping[tuple[int, str], RunSelection]  # (run_number, serial), only where a placed segment covers it
    recorded_serials: frozenset[str]


@dataclass(frozen=True)
class DerivedProbe:
    serial: str
    insertion_number: int
    electrode_config_hash: str | None
    sortable_runs: tuple[int, ...]


@dataclass(frozen=True)
class DerivedMontage:
    montage_id: int
    start_s: float
    end_s: float
    run_numbers: tuple[int, ...]
    probes: tuple[DerivedProbe, ...]  # by serial


@dataclass(frozen=True)
class PlacedPosition:
    serial: str
    insertion_number: int
    covered_runs: tuple[int, ...]


@dataclass(frozen=True)
class Derivation:
    status: Literal["not_needed", "waiting", "placed", "ambiguous"]
    positions_seq: int | None
    montages: tuple[DerivedMontage, ...]
    placed: tuple[PlacedPosition, ...]
    flags: tuple[dict, ...]  # the listing's `_flag` shape


def _flag(code: str, message: str, run_number: int | None = None) -> dict:
    return {"code": code, "message": message, "run_number": run_number, "block_number": None, "segment_id": None}


@dataclass(frozen=True)
class _Placed:
    """A position with both edges in a gap."""

    position: PositionIn
    start_gap: int
    end_gap: int
    left_out: frozenset[int]  # run numbers an `inside` edge left out


def _montage_window(runs: tuple[RunSpan, ...], lo: int, hi: int) -> tuple[float, float]:
    """`[runs[lo].start_s, runs[hi].start_s)`; the last montage ends at its last run's end (P10)."""
    if hi < len(runs):
        return runs[lo].start_s, runs[hi].start_s
    last = runs[-1]
    return runs[lo].start_s, last.end_s + ZERO_LENGTH_NUDGE_S if last.end_s == last.start_s else last.end_s


def _one_montage(runs: tuple[RunSpan, ...]) -> tuple[DerivedMontage, ...]:
    if not runs:
        return ()
    start_s, end_s = _montage_window(runs, 0, len(runs))
    return (DerivedMontage(1, start_s, end_s, tuple(r.run_number for r in runs), ()),)


def _place_edge(
    p: PositionIn, edge: Literal["insertion", "end"], at: datetime, named: int | None,
    facts: DerivationInput, flags: list[dict],
) -> tuple[int | None, int | None]:
    """The edge's gap (None when it cannot be placed) and the run an `inside` edge leaves out."""
    field = "inserted_after_run" if edge == "insertion" else "ended_after_run"
    runs = facts.runs
    who = f"probe {p.serial}'s {edge} (insertion {p.insertion_number})"
    lo, hi = window(at, facts.t0, widen_for(facts.t0_source))
    placement = place(lo, hi, runs, named_after_run=named)
    when = at.isoformat(sep=" ")
    if placement.kind == "gap":
        return placement.gap, None
    if placement.kind == "inside":
        r = placement.run_number
        flags.append(_flag(
            "position_edge_inside_run",
            f"{who} falls inside run {r}: it is placed after run {r}, and run {r} is sorted for probe {p.serial} on "
            f"neither side",
            run_number=r))
        return placement.gap, r
    if placement.kind == "ambiguous":
        where = " or ".join(gap_name(g, runs) for g in placement.candidates)
        flags.append(_flag(
            "position_edge_ambiguous",
            f"{who} at {when} could lie {where}: name the gap with {field}; no montage is listed until then"))
    elif placement.kind == "unfit":
        flags.append(_flag(
            "position_edge_ambiguous",
            f"{who} at {when} names the gap {gap_name(placement.gap, runs)} in {field}, which its time does not touch; "
            f"no montage is listed until the name or the time is corrected"))
    else:  # unknown_run
        flags.append(_flag(
            "position_edge_ambiguous",
            f"{who} names run {placement.run_number} in {field}, which is not a measured run of this session; no "
            f"montage is listed until it names one"))
    return None, None


def _wall_order(positions: tuple[PositionIn, ...]) -> list[PositionIn]:
    return sorted(positions, key=lambda p: (p.serial, p.inserted_at, p.insertion_number))


def _place_positions(facts: DerivationInput, flags: list[dict]) -> list[_Placed] | None:
    """Every position placed, or None when any edge cannot be, or two placements cannot both hold (D15)."""
    n = len(facts.runs)
    placed: list[_Placed] = []
    unplaced = False
    for p in _wall_order(facts.positions):
        start, start_out = _place_edge(p, "insertion", p.inserted_at, p.inserted_after_run, facts, flags)
        if p.ended_at is None:
            end, end_out = n, None
        else:
            end, end_out = _place_edge(p, "end", p.ended_at, p.ended_after_run, facts, flags)
        if start is None or end is None:
            unplaced = True
            continue
        placed.append(_Placed(p, start, end, frozenset(r for r in (start_out, end_out) if r is not None)))

    # D15: a position that ends before it starts, or a later position of one serial placed before an earlier one ends.
    contradicted = False
    for i, a in enumerate(placed):
        if a.end_gap < a.start_gap:
            contradicted = True
            flags.append(_flag(
                "position_edge_ambiguous",
                f"probe {a.position.serial}'s insertion {a.position.insertion_number} ends "
                f"{gap_name(a.end_gap, facts.runs)}, before it starts {gap_name(a.start_gap, facts.runs)}; no montage "
                f"is listed until its gaps are named"))
        for b in placed[i + 1:]:
            if b.position.serial == a.position.serial and b.start_gap < a.end_gap:
                contradicted = True
                flags.append(_flag(
                    "position_edge_ambiguous",
                    f"probe {a.position.serial}'s insertions {a.position.insertion_number} and "
                    f"{b.position.insertion_number} overlap once placed between runs; no montage is listed until their "
                    f"gaps are named"))
    return None if unplaced or contradicted else placed


def _splits(facts: DerivationInput, placed: list[_Placed]) -> tuple[set[int], set[tuple[int, str]]]:
    """The gaps strictly inside 1..n-1 that cut montages, and the (run_number, serial) whose selection changed inside
    the run."""
    runs = facts.runs
    n = len(runs)
    cuts: set[int] = set()
    seen: set[str] = set()
    for pl in placed:  # in wall order per serial
        if pl.position.end_kind == "moved":
            cuts.add(pl.end_gap)
        if pl.position.serial in seen:
            cuts.add(pl.start_gap)
        seen.add(pl.position.serial)

    changed_inside: set[tuple[int, str]] = set()
    serials = sorted({serial for _, serial in facts.selections})
    for serial in serials:
        previous: RunSelection | None = None
        for i, r in enumerate(runs):
            sel = facts.selections.get((r.run_number, serial))
            if sel is None:
                continue
            if previous is not None and sel.first_hash != previous.last_hash:
                cuts.add(i)
            if sel.changed:
                cuts.add(i + 1)
                changed_inside.add((r.run_number, serial))
            previous = sel
    return {g for g in cuts if 0 < g < n}, changed_inside


def _probe(
    pl: _Placed, covered: tuple[RunSpan, ...], facts: DerivationInput, changed_inside: set[tuple[int, str]],
) -> DerivedProbe:
    serial = pl.position.serial
    recorded = serial in facts.recorded_serials
    sortable = tuple(
        r.run_number for r in covered
        if r.run_number not in pl.left_out
        and (r.run_number, serial) not in changed_inside
        and (not recorded or (r.run_number, serial) in facts.selections))
    hashes = {facts.selections[(r, serial)].first_hash for r in sortable} if recorded else set()
    assert len(hashes) <= 1, f"probe {serial} has selections {sorted(hashes)} in one montage"
    return DerivedProbe(serial, pl.position.insertion_number, next(iter(hashes), None), sortable)


def derive(facts: DerivationInput) -> Derivation:
    seq = facts.positions_seq
    if not facts.has_ephys:
        return Derivation("not_needed", seq, _one_montage(facts.runs), (), ())
    if facts.positions is None or facts.t0 is None or not facts.census_complete or not facts.runs:
        return Derivation("waiting", seq, (), (), ())

    runs = facts.runs
    n = len(runs)
    flags: list[dict] = []
    placed = _place_positions(facts, flags)
    named = {p.serial for p in facts.positions}
    flags.extend(_flag(
        "probe_without_position",
        f"probe {serial} is recorded, and no position from wl.works names it: its runs are sortable in no montage, and "
        f"its area is unknown") for serial in sorted(facts.recorded_serials - named))
    if placed is None:
        return Derivation("ambiguous", seq, (), (), tuple(flags))

    cuts, changed_inside = _splits(facts, placed)
    bounds = [0, *sorted(cuts), n]
    by_serial = sorted(placed, key=lambda pl: (pl.position.serial, pl.position.insertion_number))
    montages = []
    for montage_id, (lo, hi) in enumerate(zip(bounds, bounds[1:]), start=1):
        start_s, end_s = _montage_window(runs, lo, hi)
        # A position is in the montage when it covers one of its runs; it brings only the runs it covers.
        probes = [_probe(pl, runs[max(pl.start_gap, lo):min(pl.end_gap, hi)], facts, changed_inside)
                  for pl in by_serial if max(pl.start_gap, lo) < min(pl.end_gap, hi)]
        serials = [p.serial for p in probes]
        assert len(serials) == len(set(serials)), f"two positions of one serial in montage {montage_id}: {serials}"
        montages.append(DerivedMontage(
            montage_id, start_s, end_s, tuple(r.run_number for r in runs[lo:hi]), tuple(probes)))

    placed_out = tuple(
        PlacedPosition(pl.position.serial, pl.position.insertion_number,
                       tuple(r.run_number for r in runs[pl.start_gap:pl.end_gap]))
        for pl in by_serial)
    return Derivation("placed", seq, tuple(montages), placed_out, tuple(flags))

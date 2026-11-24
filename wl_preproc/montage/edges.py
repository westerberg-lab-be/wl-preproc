"""Placing one probe-position edge among a session's runs (spec section 4.2).

wl.works' `place()` kept exactly: a position's time is a whole minute (or a time with seconds), so it is a half-open
window `[lo, hi)` in session seconds, and it is placed in the gap between runs it touches. Pure: no database, no I/O.

Gaps are indexed 0..n over the runs in start order. Gap `g` lies before run index `g`; gap 0 is open below and gap n is
open above. An `inside` placement's gap is the one after its run (`index + 1`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MANIFEST_WIDEN_S = 60.0


@dataclass(frozen=True)
class RunSpan:
    run_number: int
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Placement:
    # "unfit" is a named gap the window does not touch: `gap` is the named one, `candidates` the touched ones.
    kind: Literal["gap", "inside", "ambiguous", "unfit", "unknown_run"]
    gap: int | None = None
    run_number: int | None = None
    candidates: tuple[int, ...] = ()


def widen_for(t0_source: str | None) -> float:
    """A manifest's t0 is only to the minute, so its window widens by a minute each side; the others are exact."""
    return MANIFEST_WIDEN_S if t0_source == "manifest" else 0.0


def window(at: datetime, t0: datetime, widen_s: float) -> tuple[float, float]:
    """`[lo, hi)` in session seconds for a time `at` (naive UTC). A whole minute stands for sixty seconds; a time with
    seconds stands for the second it falls in."""
    seconds = (at - t0).total_seconds()
    if at.second == 0 and at.microsecond == 0:
        lo, hi = seconds, seconds + 60.0
    else:
        lo = float(math.floor(seconds))
        hi = lo + 1.0
    return lo - widen_s, hi + widen_s


def _finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} is not finite: {value!r}")


def place(
    lo: float, hi: float, runs: Sequence[RunSpan], named_after_run: int | None = None
) -> Placement:
    """Place the window `[lo, hi)` among `runs` (in start order); `named_after_run` is a gap a person named (0 is the
    gap before the first run) and is only trusted when the window touches it."""
    _finite("lo", lo)
    _finite("hi", hi)
    for r in runs:
        _finite(f"run {r.run_number} start_s", r.start_s)
        _finite(f"run {r.run_number} end_s", r.end_s)

    n = len(runs)
    gaps: list[int] = []
    for g in range(n + 1):
        a = -math.inf if g == 0 else runs[g - 1].end_s
        b = math.inf if g == n else runs[g].start_s
        if hi > a and lo < b:
            gaps.append(g)
    touched = tuple(gaps)

    if named_after_run is not None:
        if named_after_run == 0:
            named = 0
        else:
            idx = next((i for i, r in enumerate(runs) if r.run_number == named_after_run), None)
            if idx is None:
                return Placement("unknown_run", run_number=named_after_run)
            named = idx + 1
        if named in touched:
            return Placement("gap", gap=named)
        return Placement("unfit", gap=named, candidates=touched)

    for i, r in enumerate(runs):
        if lo >= r.start_s and hi <= r.end_s:
            return Placement("inside", gap=i + 1, run_number=r.run_number)
    if len(touched) == 1:
        return Placement("gap", gap=touched[0])
    if len(touched) >= 2:
        return Placement("ambiguous", candidates=touched)
    # Only overlapping runs reach here: the window lies in their union, so call it inside the first it overlaps.
    i = max(0, next((i for i, r in enumerate(runs) if hi > r.start_s and lo < r.end_s), 0))
    return Placement("inside", gap=i + 1, run_number=runs[i].run_number)


def gap_name(gap: int, runs: Sequence[RunSpan]) -> str:
    if gap == 0:
        return f"before run {runs[0].run_number}"
    if gap == len(runs):
        return f"after run {runs[-1].run_number}"
    return f"between runs {runs[gap - 1].run_number} and {runs[gap].run_number}"

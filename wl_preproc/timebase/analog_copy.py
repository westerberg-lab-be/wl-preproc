"""Lining the eye tracker's trace up with its analog copy on an NI channel
(design spec `2026-10-18-training-records-and-backup-clock-design.md`,
amendments 77-84).

Under the NI's clock, an eye file with no usable barcodes still has a witness
on the NI: the eye tracker writes its own values through a DAC onto the NI's
analog inputs. A window of the eye file's trace, laid against the channel,
lines up at one shift; that shift, at the window's centre, is a pair
`(ni_s, device_s)`, and pairs fit the eye's clock to the NI's as barcodes do
(`fit.py::fit_pairs_across`).

**What is compared.** At each shift, the channel's stretch is regressed on a
window of a candidate *family* of the eye's columns jointly, by least squares
on `[columns, 1]`, and the fit is scored by its multiple correlation `R`, the
square root of the variance it explains. So the DAC's scale and offset drop
out, and so does any linear mix of the family's columns: the copy is the
tracker's output, which amendment 78a does not know, and OpenIris's calibrated
gaze mixes P1-P4 X with Y. Matched against one column, such a copy's peak is
tilted by the other column's saccades, about a millisecond a window. The
families (`"Left P1-P4 (X, Y)"`, `"Left P1 (X, Y)"`, `"Left PupilWidth"`, and
the same for `Right`) are the caller's; each is an `(n_samples, k)` array, and
`find_channel` takes the best. A family with one column is Pearson's
correlation's magnitude. The eye's columns are interpolated onto the
channel's grid, `fs_hz`, before they are compared.

**How `R` is computed.** A window's columns are centred and orthonormalised
(QR, `_basis`), so `R^2` at a shift is the sum over the basis of each one's
squared sliding correlation with the channel, over the channel's moving
spread: `k` sliding correlations, by FFT for the anchor and directly for the
walk. A column constant over the window (an untracked eye) or dependent on the
others is dropped; a window with no column left is skipped.

**The pairs, in two stages,** as `strobes.py` finds its own:

* **The anchor.** Windows are tried in turn against the whole channel, by FFT
  cross-correlation, until one is distinct. The eye may run before the NI
  starts (amendment 44), and a window the NI never saw cannot anchor.
* **The walk.** Each later window is searched only within
  `RUNNER_UP_EXCLUSION_S` of the line through the pairs found so far.

**Distinct, or no pair.** A window gives a pair only when its best shift's
`R` is at least `MIN_CORRELATION` and beats the best rival shift (another
local maximum) more than `RUNNER_UP_EXCLUSION_S` from it by `MIN_PEAK_MARGIN`
(`_distinct` says why a rival, not any shift). A flat channel correlates 0; a
periodic stretch, or a fixation whose only movement is noise the copy does not
share, has rivals as good as its peak (`R` is never negative, so an
anti-correlated shift is a rival too). Each gives no pair rather than a guess.
A periodic trace never anchors at all, so it is `NoCopy`, never a match a
period off, whose residuals would be as small as a right one's. A window whose
channel sits at its rail for more than a tenth of it (a clipped copy) gives no
pair either: its clipped edges would bend the peak.

**The drop and refit** (78). The copy comes through a USB DAC driven by
software, whose delay jumps in a stall (amendment 84: 2% of frames 10 ms or
more late, up to 50 ms). A stalled window lines up off the line, inside the
search. The pair furthest from the fitted line is dropped and the line
refitted until every pair is within `RESIDUAL_BOUND_US`, up to
`MAX_DROPPED_FRACTION` of the pairs; beyond that the copy is refused. The
dropped stretches are named, since the eye's time there is the line's.

**Time.** The eye's is its own device seconds, from frame numbers, so a dropped
frame leaves a gap rather than shifting every later sample. The channel's
sample `k` is at `k / fs_hz` seconds from the channel's first sample; what
that instant means against the NI's native samples is the reading layer's
(`read_xa_channels`: each decimated sample is its block's mean, stamped at the
block's centre).

**Memory.** The anchor's FFT and its normalisation span the channel given
(one decimated channel, float64, about 1.4 GB at the peak for 12 h at 1 kHz),
and `match_copy` releases them when the anchor is found. A family's columns are
correlated one at a time and their squares summed, so two columns add one
channel-length array, not two. The walk holds a window's worth of arrays at a
time. `find_channel` keeps every channel's spectrum for its prefix (about
10 MB a channel for 600 s), since every family searches them all.

**Reading the files** (`place_eye_by_copy`, at the end). The matcher above is
pure numeric: no DataJoint, no file I/O, no `wl_preproc.synth`, as `fit.py`.
The reading layer reads the session's first NI file's XA channels, decimated
(`read_xa_channels`), and the eye file's families (`eye_candidates`), finds the
channel on every channel's first `SEARCH_PREFIX_S`, and matches the chosen one
whole. The rest is the caller's: the fit, the lag (amendment 79), the rows.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np
import scipy.fft
import scipy.linalg

from wl_preproc.eye.ohdpi import EYES, P1_X, P1_Y, P4_X, P4_Y, PUPIL_WIDTH, read_columns, read_ohdpi
from wl_preproc.timebase import channels as _channels
from wl_preproc.timebase._nidq_meta import read_nidq_meta
from wl_preproc.timebase.fit import fit_pairs_across

logger = logging.getLogger(__name__)

# Every constant below was set on synthetic data (Task 1's generator, and this
# module's tests) and is revisited on real traces with their copy
# (`docs/hardware-checks.md` item 17).

#: The rate both sides are compared at, after the channel's decimation: a
#: millisecond, so each 2 ms eye frame spans two samples, and the parabola
#: refines below one. Set on synthetic data; revisited on real traces
#: (hardware check 17).
DECIMATED_RATE_HZ = 1000.0
#: Each window's length: about 30 fixations and their saccades, enough to be
#: distinct; short enough that the line so far predicts it within a sample.
#: Set on synthetic data; revisited on real traces (hardware check 17).
WINDOW_S = 10.0
#: From one window's start to the next's: windows abut, so each sample is
#: compared once. Set on synthetic data; revisited on real traces (hardware check 17).
STEP_S = 10.0
#: `find_channel` looks at the eye's first this many seconds: enough windows to
#: tell a channel by, without reading a 12 h file whole for each. Set on
#: synthetic data; revisited on real traces (hardware check 17).
SEARCH_PREFIX_S = 600.0
#: A window's best shift must reach at least this multiple correlation `R`. The
#: synthetic copy, a mix of P1-P4 X and Y through `tanh`, reaches about 0.96
#: against the family jointly. Set on synthetic data; revisited on real traces
#: (hardware check 17).
MIN_CORRELATION = 0.9
#: ... and beat the best rival (a local maximum) outside `RUNNER_UP_EXCLUSION_S`
#: of it by this. Set on synthetic data; revisited on real traces (hardware check 17).
MIN_PEAK_MARGIN = 0.1
#: The half-width of a peak (a saccade's duration, about), beyond which a local
#: maximum is a rival; and the half-width of each later window's search about
#: the line so far. Set on synthetic data; revisited on real traces (hardware check 17).
RUNNER_UP_EXCLUSION_S = 0.05
#: Fewer distinct windows than this is no copy (absent); fewer pairs than this
#: left after the drop is a refusal. Set on synthetic data; revisited on real
#: traces (hardware check 17).
MIN_PAIRS = 20
#: A pair further than this from the fitted line is dropped: a quarter of a
#: 2 ms frame. Set on synthetic data; revisited on real traces (hardware check 17).
RESIDUAL_BOUND_US = 500.0
#: At most this fraction of the pairs may be dropped; more, and the copy is
#: refused. Set on synthetic data; revisited on real traces (hardware check 17).
MAX_DROPPED_FRACTION = 0.1
#: A measured lag must be at most this (Task 4's check, amendment 79): far
#: above the copy's 4-5 ms of output latency, far below the spacing of
#: saccades. Set on synthetic data; revisited on real traces (hardware check 17).
LAG_CEILING_S = 0.03


def _eye_families(eye: str) -> dict[str, tuple[tuple[str, ...], ...]]:
    return {
        f"{eye} P1-P4 (X, Y)": ((eye + P1_X, eye + P4_X), (eye + P1_Y, eye + P4_Y)),
        f"{eye} P1 (X, Y)": ((eye + P1_X,), (eye + P1_Y,)),
        f"{eye} PupilWidth": ((eye + PUPIL_WIDTH,),),
    }


#: The candidates for what the DAC puts out (amendment 78a), each a family the
#: matcher fits jointly: name to its columns, each column one eye-file column
#: (`("LeftCR1X",)`) or the first less the second (`("LeftCR1X", "LeftCR4X")`,
#: P1-P4 X). In order, `Left` before `Right`, as `eye_candidates` returns them.
#: Each name is at most 32 characters, the width of the `signal` column it is
#: stored in.
FAMILIES: Mapping[str, tuple[tuple[str, ...], ...]] = MappingProxyType(
    {name: columns for eye in EYES for name, columns in _eye_families(eye).items()}
)

# A window more than this fraction missing (NaN, or in a gap of dropped frames)
# gives no pair: its correlation would rest on interpolation.
_MAX_MISSING_FRACTION = 0.1
# Eye samples further apart than this many frame periods (the window's median
# spacing) leave the grid points between them missing.
_MAX_GAP_FRAMES = 1.5
# A channel stretch whose spread is below this fraction of the channel's whole
# spread is flat: a stuck DAC, or the rail. Its correlation is 0, not a ratio
# of two rounding errors. A thousand times above float64 rounding of the
# chunked sums below, and below any real signal (the NI's own 16-bit step).
_FLAT_SPREAD_FRACTION = 1e-4
# A trace window's column whose spread is below this fraction of its largest
# value is constant (an untracked eye's zeros): dropped, never divided by.
_CONSTANT_SPREAD_FRACTION = 1e-12
# A window's column is dependent on the others (collinear with them) when, at
# unit length, less than this of it lies outside their span: dropped, since its
# direction there is rounding. Far above float64 rounding, far below any
# independent signal.
_DEPENDENT_FRACTION = 1e-9
# The channel's moving spread is computed over chunks of this many shifts, each
# from its own centred cumulative sums, so rounding stays local to a chunk
# rather than growing over a 12 h channel.
_SPREAD_CHUNK = 1 << 16
# Each later window is correlated over this many exclusion widths either side
# of the line, so that a peak anywhere inside the search still has rivals a
# full exclusion width beyond it on both sides.
_LOCAL_SPAN_WIDTHS = 3


class NoCopy(Exception):
    """No copy: no channel, or no candidate, gives `MIN_PAIRS` distinct windows.
    The eye file falls back to its barcodes (amendment 77's 'absent')."""


class CopyRefused(Exception):
    """A copy was found but does not fit: too few pairs, or too many dropped
    for the residual to be met. The message says which (amendment 77's 'failed')."""


@dataclass(frozen=True)
class CopyMatch:
    """The pairs that place the eye file on the NI's clock.

    `pairs` are `(ni_s, device_s)`, one per kept window at its centre, in
    device order, every one within `RESIDUAL_BOUND_US` of their line.
    `channel` and `signal` are the caller's to fill (`dataclasses.replace`):
    `match_copy` is given one channel and one family's columns and knows
    neither's name, so it returns -1 and ''. `n_windows` counts the windows tried, `n_dropped`
    the pairs the refit dropped, and `dropped_stretches` the device-time extents
    of those pairs' windows, adjacent ones merged.
    """

    pairs: tuple[tuple[float, float], ...]
    channel: int
    signal: str
    n_windows: int
    n_dropped: int
    dropped_stretches: tuple[tuple[float, float], ...]


def find_channel(
    eye_time_s: np.ndarray, candidates: dict[str, np.ndarray], channels: list[np.ndarray], fs_hz: float
) -> tuple[int, str]:
    """The `(channel, signal)` that carries the copy: the most distinct windows
    over the eye's first `SEARCH_PREFIX_S`, against the channels' prefixes as
    given. `candidates` maps a family's name (`"Left P1-P4 (X, Y)"`) to its
    columns, `(n_samples, k)`; a 1-D array is one column. A tie (the two eyes
    both follow the gaze) goes to the higher summed peak `R`. A constant
    family (an untracked eye) gives no window, and so no score.

    Raises `NoCopy` when the best gives fewer than `MIN_PAIRS`.
    """
    eye_time_s = np.asarray(eye_time_s, dtype=np.float64)
    if not channels or not candidates:
        raise NoCopy(f"{len(channels)} analog channels and {len(candidates)} candidate traces: nothing to match")
    starts = _window_starts(eye_time_s, limit_s=SEARCH_PREFIX_S)
    references = [_Reference(channel, fs_hz) for channel in channels]
    best: tuple[int, float] = (-1, -math.inf)
    choice: tuple[int, str] | None = None
    for signal, family in candidates.items():
        trace = _columns(family)
        _check_lengths(eye_time_s, trace)
        walks = [_Walk(reference, release_on_anchor=False) for reference in references]
        for start in starts:
            # At a rate of 1: the windows are shared by every channel's walk, and
            # only counted here, not timed.
            window = _window(eye_time_s, trace, start, fs_hz, 1.0)
            if window is None:
                continue
            for walk in walks:
                walk.offer(window)
        for index, walk in enumerate(walks):
            score = (len(walk.found), sum(hit.correlation for hit in walk.found))
            if score > best:
                best, choice = score, (index, signal)
    if choice is None or best[0] < MIN_PAIRS:
        named = "none" if choice is None else f"channel {choice[0]}'s {choice[1]!r}"
        raise NoCopy(
            f"the best match ({named}) gave {max(best[0], 0)} distinct windows of {len(starts)} "
            f"in the first {SEARCH_PREFIX_S:g} s, and a copy needs {MIN_PAIRS}"
        )
    return choice


def match_copy(eye_time_s: np.ndarray, trace: np.ndarray, channel: np.ndarray, fs_hz: float) -> CopyMatch:
    """Every window of `trace` (on `eye_time_s`, the eye's own seconds; NaN
    is missing) placed on `channel` (decimated to `fs_hz`), dropped to its line.
    `trace` is one family's columns, `(n_samples, k)`, fitted jointly; a 1-D
    array is one column.

    Raises `NoCopy` below `MIN_PAIRS` distinct windows, and `CopyRefused` when
    the drop and refit cannot meet `RESIDUAL_BOUND_US` within
    `MAX_DROPPED_FRACTION`, or leaves fewer than `MIN_PAIRS`.
    """
    eye_time_s = np.asarray(eye_time_s, dtype=np.float64)
    trace = _columns(trace)
    _check_lengths(eye_time_s, trace)
    starts = _window_starts(eye_time_s)
    walk = _Walk(_Reference(channel, fs_hz), release_on_anchor=True)
    for start in starts:
        window = _window(eye_time_s, trace, start, fs_hz, walk.scale)
        if window is not None:
            walk.offer(window)
    if len(walk.found) < MIN_PAIRS:
        raise NoCopy(
            f"{len(walk.found)} of {len(starts)} windows matched the channel distinctly, "
            f"and a copy needs {MIN_PAIRS}"
        )
    kept, dropped = _drop_and_refit(walk.found)
    return CopyMatch(
        pairs=tuple((hit.ni_s, hit.device_s) for hit in kept),
        channel=-1,
        signal="",
        n_windows=len(starts),
        n_dropped=len(dropped),
        dropped_stretches=_stretches(dropped),
    )


@dataclass(frozen=True)
class _Hit:
    ni_s: float
    device_s: float  # the window's centre
    start_s: float  # the window's start, device seconds
    correlation: float


class _Window:
    """One window of a family's columns on the channel's grid, as an orthonormal
    basis of their centred span (`units`, one row each), scaled so that each
    row's dot product with a channel stretch, over that stretch's spread, is
    its correlation; their squares sum to the joint fit's `R^2`."""

    def __init__(self, start_s: float, units: np.ndarray, device_step_s: float) -> None:
        self.start_s = start_s
        self.units = units
        self.centre_s = start_s + (units.shape[1] - 1) / 2 * device_step_s
        self._spectrum: tuple[int, np.ndarray] | None = None

    def spectrum(self, length: int) -> np.ndarray:
        """`conj(rfft(units, length))` per row, kept for the next channel of the
        same length."""
        if self._spectrum is None or self._spectrum[0] != length:
            self._spectrum = (length, np.conj(scipy.fft.rfft(self.units, length, axis=-1, workers=-1)))
        return self._spectrum[1]


class _Reference:
    """One channel, with what its anchor search needs, made once and released
    when nothing needs it any more."""

    def __init__(self, channel: np.ndarray, fs_hz: float) -> None:
        self.fs_hz = fs_hz
        self.n = _window_samples(fs_hz)
        self.exclusion = round(RUNNER_UP_EXCLUSION_S * fs_hz)
        values = np.asarray(channel, dtype=np.float64)
        self.values = values - (float(np.mean(values)) if values.size else 0.0)
        spread = math.sqrt(float(np.dot(self.values, self.values)) / values.size) if values.size else 0.0
        # Variances at or below this are flat (`_FLAT_SPREAD_FRACTION`).
        self.flat_variance = (_FLAT_SPREAD_FRACTION * spread) ** 2
        # The channel's extremes: where a copy is clipped, the rails it holds.
        self.rails = (float(np.min(self.values)), float(np.max(self.values))) if values.size else (0.0, 0.0)
        self.n_shifts = max(values.size - self.n + 1, 0)
        self._spectrum: np.ndarray | None = None
        self._spread: np.ndarray | None = None

    @property
    def fft_length(self) -> int:
        return scipy.fft.next_fast_len(self.values.size, real=True)

    def correlations(self, window: _Window) -> np.ndarray:
        """The window's multiple correlation `R` at every shift that lies wholly
        inside the channel, by FFT, one basis row at a time; 0 where the
        channel is flat."""
        length = self.fft_length
        if self._spectrum is None:
            self._spectrum = scipy.fft.rfft(self.values, length, workers=-1)
            self._spread = _moving_spread(self.values, self.n, self.flat_variance)
        squares = np.zeros(self.n_shifts)
        product = np.empty_like(self._spectrum)
        for spectrum in window.spectrum(length):
            np.multiply(self._spectrum, spectrum, out=product)
            dot = scipy.fft.irfft(product, length, workers=-1)[: self.n_shifts]
            np.multiply(dot, dot, out=dot)
            squares += dot
        return _multiple(squares, self._spread)

    def local_correlations(self, window: _Window, lo: int, hi: int) -> np.ndarray:
        """The window's multiple correlation `R` at shifts `lo..hi` inclusive,
        directly."""
        stretch = self.values[lo : hi + self.n]
        squares = np.zeros(hi - lo + 1)
        for unit in window.units:
            dot = np.correlate(stretch, unit, mode="valid")
            squares += dot * dot
        return _multiple(squares, _moving_spread(stretch, self.n, self.flat_variance))

    def release(self) -> None:
        """Drop the anchor's whole-channel arrays; made again if asked for."""
        self._spectrum = None
        self._spread = None

    def clipped(self, shift: int) -> bool:
        """Whether more than `_MAX_MISSING_FRACTION` of the channel's stretch at
        `shift` sits at one of its extremes: the copy clipped at the NI's
        range, which is missing, as NaN is in the trace. Its remainder still
        correlates, but the clip bends the edges it keeps, and a peak from
        them is off by a fraction of a millisecond."""
        stretch = self.values[shift : shift + self.n]
        at_rail = np.count_nonzero((stretch == self.rails[0]) | (stretch == self.rails[1]))
        return at_rail > _MAX_MISSING_FRACTION * self.n


class _Walk:
    """One family's search along one channel: the anchor, then the walk."""

    def __init__(self, reference: _Reference, *, release_on_anchor: bool) -> None:
        self.reference = reference
        # `match_copy`'s channel is whole and walked once, so its anchor's arrays
        # go as soon as it is found; `find_channel`'s are prefixes, shared by
        # every family's walk.
        self._release_on_anchor = release_on_anchor
        self.found: list[_Hit] = []
        # The line so far: ni_s = ni_mean + (device_s - device_mean) / scale.
        self._line = (0.0, 0.0, 1.0)

    def offer(self, window: _Window) -> None:
        if self.reference.n_shifts < 3:
            return
        hit = self._anchor(window) if not self.found else self._next(window)
        if hit is None:
            return
        if not self.found and self._release_on_anchor:
            self.reference.release()
        self.found.append(hit)
        self._refit()

    def _anchor(self, window: _Window) -> _Hit | None:
        ref = self.reference
        correlation = ref.correlations(window)
        peak = int(np.argmax(correlation))
        if not _distinct(correlation, peak, ref.exclusion):
            return None
        if peak < 1 or peak > ref.n_shifts - 2:
            return None
        # Refined from the direct correlation, not the FFT's: the same numbers
        # the walk refines from.
        return self._hit(window, ref.local_correlations(window, peak - 1, peak + 1), peak - 1, 1)

    def _next(self, window: _Window) -> _Hit | None:
        ref = self.reference
        ni_mean, device_mean, scale = self._line
        predicted_ni_s = ni_mean + (window.centre_s - device_mean) / scale
        centre = round(predicted_ni_s * ref.fs_hz - (ref.n - 1) / 2)
        span = _LOCAL_SPAN_WIDTHS * ref.exclusion
        lo, hi = max(centre - span, 0), min(centre + span, ref.n_shifts - 1)
        # The peak is looked for within the exclusion of the line, and away from
        # the ends, so that the parabola has both neighbours.
        first, last = max(centre - ref.exclusion, lo + 1), min(centre + ref.exclusion, hi - 1)
        if first > last:
            return None
        correlation = ref.local_correlations(window, lo, hi)
        peak = first - lo + int(np.argmax(correlation[first - lo : last - lo + 1]))
        if not _distinct(correlation, peak, ref.exclusion):
            return None
        return self._hit(window, correlation, lo, peak)

    def _hit(self, window: _Window, correlation: np.ndarray, lo: int, peak: int) -> _Hit | None:
        """The pair at the window's centre, the peak refined below a sample by
        the parabola through it and its two neighbours; None when the channel
        there is clipped."""
        if self.reference.clipped(lo + peak):
            return None
        before, at, after = (float(v) for v in correlation[peak - 1 : peak + 2])
        curvature = before - 2.0 * at + after
        step = 0.0 if curvature >= 0.0 else min(max(0.5 * (before - after) / curvature, -0.5), 0.5)
        shift = lo + peak + step
        ref = self.reference
        ni_s = (shift + (ref.n - 1) / 2) / ref.fs_hz
        return _Hit(ni_s=ni_s, device_s=window.centre_s, start_s=window.start_s, correlation=at)

    def _refit(self) -> None:
        pairs = [(hit.ni_s, hit.device_s) for hit in self.found]
        ni_mean = sum(ni for ni, _ in pairs) / len(pairs)
        device_mean = sum(device for _, device in pairs) / len(pairs)
        # Until two pairs exist, a rate of 1: both sides are seconds.
        scale = 1.0 if len(pairs) < 2 else fit_pairs_across([pairs], 1.0).scale
        self._line = (ni_mean, device_mean, scale)

    @property
    def scale(self) -> float:
        """Device seconds per NI second, on the line so far."""
        return self._line[2]


def _distinct(correlation: np.ndarray, peak: int, exclusion: int) -> bool:
    """Whether the peak's `R` is at least `MIN_CORRELATION` and beats the best
    rival shift, more than `exclusion` samples from it, by `MIN_PEAK_MARGIN`.

    A rival is a local maximum: a shift at least as good as its neighbours (an
    end, as good as its one). Not merely any shift: the peak's own shoulder
    just past the exclusion is the same alignment, a few saccades fewer, and a
    gaze trace fixating for about 300 ms at a time still correlates about 0.87
    with itself 50 ms on, so the shoulder would refuse a third of good windows
    while telling nothing about another alignment. A period, a repeated
    pattern, a fixation's noise: each is another local maximum, and refuses.
    With no rival within reach (a walk's search is 150 ms either way), the peak
    is the only alignment there, and distinct."""
    best = float(correlation[peak])
    if not best >= MIN_CORRELATION:
        return False
    rival = np.ones(correlation.size, dtype=bool)
    rival[1:] &= correlation[1:] >= correlation[:-1]
    rival[:-1] &= correlation[:-1] >= correlation[1:]
    rival[max(peak - exclusion, 0) : peak + exclusion + 1] = False
    return not rival.any() or best - float(np.max(correlation[rival])) >= MIN_PEAK_MARGIN


def _drop_and_refit(found: Sequence[_Hit]) -> tuple[list[_Hit], list[_Hit]]:
    """The pair furthest from the line dropped and the line refitted, until every
    pair is within `RESIDUAL_BOUND_US`, at most `MAX_DROPPED_FRACTION` of them."""
    kept, dropped = list(found), []
    limit = int(MAX_DROPPED_FRACTION * len(found))
    while True:
        pairs = [(hit.ni_s, hit.device_s) for hit in kept]
        # A nominal rate of 1.0: both sides are seconds, so the scale is the rate.
        fit = fit_pairs_across([pairs], 1.0)
        if fit.residual_us_max <= RESIDUAL_BOUND_US:
            break
        residuals = _residuals_us(pairs, fit.scale)
        if len(dropped) >= limit:
            off = len(dropped) + int(np.sum(residuals > RESIDUAL_BOUND_US))
            raise CopyRefused(
                f"{off} of {len(found)} pairs, {round(100 * off / len(found))}% of the pairs, lie further than "
                f"{RESIDUAL_BOUND_US:g} us from the line, and at most {MAX_DROPPED_FRACTION:.0%} may be dropped; "
                f"after dropping {len(dropped)}, the residual is still {fit.residual_us_max:.0f} us"
            )
        dropped.append(kept.pop(int(np.argmax(residuals))))
    if len(kept) < MIN_PAIRS:
        raise CopyRefused(
            f"too few pairs: {len(kept)} remain after dropping {len(dropped)} off the line, "
            f"and a copy needs {MIN_PAIRS}"
        )
    dropped.sort(key=lambda hit: hit.start_s)
    return kept, dropped


def _residuals_us(pairs: Sequence[tuple[float, float]], scale: float) -> np.ndarray:
    """Each pair's distance from the line `fit_pairs_across` fitted, in the NI's
    frame, as it computes its own maximum."""
    ni, device = np.array(pairs, dtype=np.float64).T
    intercept = float(np.mean(device)) - scale * float(np.mean(ni))
    return np.abs((device - intercept) / scale - ni) * 1e6


def _stretches(dropped: Sequence[_Hit]) -> tuple[tuple[float, float], ...]:
    """The dropped pairs' windows, in device seconds, adjacent ones merged."""
    merged: list[list[float]] = []
    for hit in dropped:
        start, stop = hit.start_s, hit.start_s + WINDOW_S
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    return tuple((start, stop) for start, stop in merged)


def _window_samples(fs_hz: float) -> int:
    return round(WINDOW_S * fs_hz)


def _window_starts(eye_time_s: np.ndarray, limit_s: float | None = None) -> list[float]:
    """Each window's start, `STEP_S` apart from the eye's first sample, every
    window wholly inside the trace (and inside its first `limit_s`, if given)."""
    if eye_time_s.size < 2:
        return []
    first, end = float(eye_time_s[0]), float(eye_time_s[-1])
    if limit_s is not None:
        end = min(end, first + limit_s)
    count = max(math.floor((end - first - WINDOW_S) / STEP_S + 1e-9) + 1, 0)
    return [first + k * STEP_S for k in range(count)]


def _window(
    eye_time_s: np.ndarray, trace: np.ndarray, start_s: float, fs_hz: float, scale: float
) -> _Window | None:
    """The family's columns (`trace`, `(n_samples, k)`) from `start_s` for
    `WINDOW_S`, on the channel's grid, as `_basis` of them; None when too much
    of it is missing (a sample missing in any column is missing), or every
    column is constant.

    The grid is the channel's, carried into device time by `scale` (device
    seconds per NI second, from the line so far): at 100 ppm a window on the
    device's own grid is a sample longer than its copy, and its peak then
    favours whichever end has the saccades, by up to half a sample."""
    n = _window_samples(fs_hz)
    device_step_s = scale / fs_hz
    grid = start_s + np.arange(n) * device_step_s
    lo = max(int(np.searchsorted(eye_time_s, grid[0], side="right")) - 1, 0)
    hi = min(int(np.searchsorted(eye_time_s, grid[-1], side="left")) + 1, eye_time_s.size)
    times = eye_time_s[lo:hi]
    values = np.asarray(trace[lo:hi], dtype=np.float64)
    finite = np.all(np.isfinite(values), axis=1)
    if times.size < 2 or np.count_nonzero(finite) < 2:
        return None
    # A grid point is missing unless both samples bracketing it are present and
    # no further apart than a frame and a half.
    period = float(np.median(np.diff(times)))
    left = np.clip(np.searchsorted(times, grid, side="right") - 1, 0, times.size - 2)
    bracketed = (times[left] <= grid) & (grid <= times[left + 1])
    present = bracketed & finite[left] & finite[left + 1] & (times[left + 1] - times[left] <= _MAX_GAP_FRAMES * period)
    if np.count_nonzero(present) < (1.0 - _MAX_MISSING_FRACTION) * n:
        return None
    on_grid = np.column_stack([np.interp(grid, times[finite], column[finite]) for column in values.T])
    basis = _basis(on_grid)
    if basis is None:
        return None
    return _Window(start_s, basis.T / math.sqrt(n), device_step_s)


def _basis(on_grid: np.ndarray) -> np.ndarray | None:
    """An orthonormal basis, `(n, r)`, of the span of `on_grid`'s centred
    columns, whose projection of a centred stretch is the least-squares fit on
    `[columns, 1]`. A constant column is dropped, and so is one dependent on
    the others (pivoted QR on unit columns, `_DEPENDENT_FRACTION`); None when
    none is left."""
    centred = on_grid - np.mean(on_grid, axis=0)
    spread = np.sqrt(np.mean(centred * centred, axis=0))
    varies = spread > _CONSTANT_SPREAD_FRACTION * np.max(np.abs(on_grid), axis=0)
    if not varies.any():
        return None
    columns = centred[:, varies] / (spread[varies] * math.sqrt(on_grid.shape[0]))
    q, r, _ = scipy.linalg.qr(columns, mode="economic", pivoting=True)
    rank = int(np.count_nonzero(np.abs(np.diag(r)) > _DEPENDENT_FRACTION))
    return q[:, :rank]


def _moving_spread(values: np.ndarray, n: int, flat_variance: float) -> np.ndarray:
    """The population standard deviation of every `n` consecutive values, 0
    where it is flat. Chunked, each chunk from its own centred sums."""
    n_shifts = values.size - n + 1
    spread = np.zeros(max(n_shifts, 0))
    for a in range(0, n_shifts, _SPREAD_CHUNK):
        b = min(a + _SPREAD_CHUNK, n_shifts)
        stretch = values[a : b + n - 1]
        stretch = stretch - np.mean(stretch)
        sums = np.concatenate(([0.0], np.cumsum(stretch)))
        squares = np.concatenate(([0.0], np.cumsum(stretch * stretch)))
        mean = (sums[n:] - sums[:-n]) / n
        variance = (squares[n:] - squares[:-n]) / n - mean * mean
        spread[a:b] = np.sqrt(np.where(variance > flat_variance, variance, 0.0))
    return spread


def _multiple(squares: np.ndarray, spread: np.ndarray) -> np.ndarray:
    """`R`, `sqrt(squares) / spread`, 0 where the spread is (flat channel)."""
    return np.divide(np.sqrt(squares), spread, out=np.zeros_like(squares), where=spread > 0.0)


def _columns(trace: np.ndarray) -> np.ndarray:
    """A family's columns as `(n_samples, k)`; a 1-D trace is one column."""
    trace = np.asarray(trace)
    if trace.ndim == 1:
        return trace[:, None]
    if trace.ndim != 2 or trace.shape[1] < 1:
        raise ValueError(f"a family's columns are (n_samples, k), not {trace.shape}")
    return trace


def _check_lengths(eye_time_s: np.ndarray, trace: np.ndarray) -> None:
    if len(trace) != eye_time_s.size:
        raise ValueError(f"a trace of {len(trace)} samples on {eye_time_s.size} eye times")


# ---------------------------------------------------------------------------
# Reading the files: the session's NI file and its eye file.


@dataclass(frozen=True)
class CopyPlacement:
    """An eye file placed on the NI's clock by its copy.

    `match`'s pairs are `(session_s, device_s)`: the NI's seconds from the
    first NI file's first sample (session time under the NI), and the eye's
    own seconds from its first frame, by frame number. `match.channel` is the
    XA channel's index among the XA channels (`XA<k>`), and `match.signal` the
    family's name (`FAMILIES`). `first_sample` is the native index, in the eye
    file's rows, of the eye sample at the first pair: the row whose frame is
    nearest the first pair's device time (the earlier at a tie)."""

    match: CopyMatch
    first_sample: int


def place_eye_by_copy(session_dir: Path, eye_path: Path) -> CopyPlacement:
    """Place the eye file at `eye_path` on the session's NI clock by its copy.

    The NI file is the session's first (`clock.nidq_files`), the one on session
    time. The channel is found on every XA channel's first `SEARCH_PREFIX_S`
    (`find_channel`), and only that channel is then read whole (`match_copy`).

    **The prefix is counted from the NI's first sample.** It is meant to cover
    the eye's own first `SEARCH_PREFIX_S`, which `find_channel` searches, but
    where the eye file starts on the NI is what the copy is to find: nothing
    places it before. The rig starts both recordings at the session's start
    (in the synthetic sessions, 0.1 s apart), so the eye's start is taken as
    the NI's. An eye file that started more than about `SEARCH_PREFIX_S -
    MIN_PAIRS * STEP_S` after the NI leaves too few of its windows inside the
    prefix, and is `NoCopy`: a fallback to its barcodes, never a wrong match.

    Raises `NoCopy` or `CopyRefused`, after logging its sentence as a WARNING
    naming the eye file, as `intan.py::place_intan` does: nothing in
    `wl_preproc` configures logging, so an INFO line would never be seen.
    """
    from wl_preproc.timebase.clock import nidq_files

    ni_path = nidq_files(session_dir)[0]
    _, fs_hz = _decimation(read_nidq_meta(ni_path.with_suffix(".meta")).sample_rate_hz)
    eye_time_s, candidates = eye_candidates(eye_path)
    try:
        start_s, prefixes = read_xa_channels(ni_path, SEARCH_PREFIX_S)
        channel, signal = find_channel(eye_time_s, candidates, prefixes, fs_hz)
        # The prefixes go before the chosen channel is read whole.
        del prefixes
        _, (whole,) = read_xa_channels(ni_path, None, channels=[channel])
        match = match_copy(eye_time_s, candidates[signal], whole, fs_hz)
    except (NoCopy, CopyRefused) as refusal:
        logger.warning("%s: %s", eye_path, refusal)
        raise
    pairs = tuple((start_s + ni_s, device_s) for ni_s, device_s in match.pairs)
    placed = dataclasses.replace(match, pairs=pairs, channel=channel, signal=signal)
    return CopyPlacement(match=placed, first_sample=_nearest_row(eye_time_s, pairs[0][1]))


def read_xa_channels(
    ni_path: Path, stop_s: float | None, channels: Sequence[int] | None = None
) -> tuple[float, list[np.ndarray]]:
    """The NI file's XA channels (the census's non-multiplexed analog ones,
    `XA<k>`), every one or those `channels` names, in that order, decimated by
    block means to about `DECIMATED_RATE_HZ`, as float32 in the file's int16
    counts. Each is streamed through `channels.column_chunks`, a block-aligned
    chunk at a time, and no row at or past `stop_s` (NI seconds from the file's
    first sample; None, the whole file) is read. Only whole blocks are kept.

    **A decimated sample's time is its block's centre.** The mean of the block
    of native samples `j*B` to `j*B + B - 1` stands for the instant
    `(j*B + (B - 1) / 2) / niSampRate`: 14.5 samples after the block's first at
    30 kHz, not its first. So the first float returned is the first decimated
    sample's NI time, `(B - 1) / 2 / niSampRate`, and sample `j` is at that
    plus `j / fs_hz`, `fs_hz` being `_decimation`'s rate.
    """
    meta = read_nidq_meta(ni_path.with_suffix(".meta"))
    block, _ = _decimation(meta.sample_rate_hz)
    first_xa = meta.n_analog_channels - meta.n_xa_channels
    wanted = list(range(meta.n_xa_channels)) if channels is None else list(channels)
    for k in wanted:
        if not 0 <= k < meta.n_xa_channels:
            raise ValueError(f"{ni_path}: XA channel {k} is not among its {meta.n_xa_channels}")
    rows = _channels.n_rows(ni_path, meta.n_channels, np.int16)
    if stop_s is not None:
        rows = min(rows, math.floor(stop_s * meta.sample_rate_hz))
    n_blocks = max(rows, 0) // block
    chunk_rows = block * max(_channels.CHUNK_ROWS // block, 1)
    decimated = []
    for k in wanted:
        out = np.empty(n_blocks, dtype=np.float32)
        filled = 0
        chunks = _channels.column_chunks(
            ni_path, meta.n_channels, first_xa + k, np.int16, chunk_rows=chunk_rows, stop_row=n_blocks * block
        )
        for chunk in chunks:
            counts = chunk.view(np.int16).astype(np.float64)
            means = counts.reshape(-1, block).mean(axis=1)
            out[filled : filled + means.size] = means
            filled += means.size
        decimated.append(out)
    return (block - 1) / 2 / meta.sample_rate_hz, decimated


def eye_candidates(eye_path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """The eye file's own seconds and its `FAMILIES`, in that order.

    The seconds are `(frame_number - first_frame_number) / fs_hz`, `fs_hz`
    being `eye.ohdpi.read_ohdpi`'s, so a dropped frame leaves a gap rather than
    shifting every later sample. Each family is `(n_rows, k)` float64, one
    column per entry of `FAMILIES`."""
    recording = read_ohdpi(eye_path)
    frames = np.asarray(recording.frame_numbers, dtype=np.int64)
    eye_time_s = (frames - frames[0]) / recording.fs_hz
    names = list(dict.fromkeys(name for family in FAMILIES.values() for column in family for name in column))
    data = read_columns(eye_path, names)
    candidates = {
        signal: np.column_stack([_family_column(data, column) for column in family])
        for signal, family in FAMILIES.items()
    }
    return eye_time_s, candidates


def _family_column(data: Mapping[str, np.ndarray], column: tuple[str, ...]) -> np.ndarray:
    """One family column: a file column, or the first less the second."""
    values = np.asarray(data[column[0]], dtype=np.float64)
    if len(column) == 2:
        return values - np.asarray(data[column[1]], dtype=np.float64)
    if len(column) != 1:
        raise ValueError(f"a family column is one name or two, not {column!r}")
    return values


def _decimation(sample_rate_hz: float) -> tuple[int, float]:
    """The block length `B` and the decimated rate, `niSampRate / B`.

    `B` is `niSampRate / DECIMATED_RATE_HZ` rounded to an integer, never below
    1: SpikeGLX's `niSampRate` is a calibrated rate (30000.123 Hz, say), so the
    ratio is rarely whole. The rate is then `niSampRate / B` exactly, not
    `DECIMATED_RATE_HZ`, so that decimated sample `j` is at `j * B` native
    samples, with no error growing over the file (at 30000.123 Hz, 1000.004 Hz)."""
    block = max(round(sample_rate_hz / DECIMATED_RATE_HZ), 1)
    return block, sample_rate_hz / block


def _nearest_row(eye_time_s: np.ndarray, device_s: float) -> int:
    """The row whose time is nearest `device_s`, the earlier at a tie."""
    right = int(np.searchsorted(eye_time_s, device_s, side="left"))
    if right == 0:
        return 0
    if right == eye_time_s.size:
        return right - 1
    left = right - 1
    return left if device_s - eye_time_s[left] <= eye_time_s[right] - device_s else right

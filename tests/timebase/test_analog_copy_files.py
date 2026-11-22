"""An eye file placed by its analog copy on the NI: the reading layer (design
spec `2026-10-18-training-records-and-backup-clock-design.md`, amendments
77-84).

Every session here is the synthetic generator's, with no sync box (the NI keeps
time): its NI file carries the copy on an XA channel the tests do not hand the
code, and its eye file is OpenIris's format. The truth is the generator's own
map from an eye frame to the NI instant its copy stands for, rebuilt here from
the recipe's numbers and the generator's constants, not read back from the
code under test.
"""

from __future__ import annotations

import datetime
import logging
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from wl_sync.session import SessionId

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.eye.ohdpi import read_columns
from wl_preproc.synth.faults import drop_ohdpi_frames
from wl_preproc.synth.ohdpi import FILENAME, OHDPI_FPS, OHDPI_PRE_ROLL_S, frame_count
from wl_preproc.synth.recipe import EYE_RECIPE, BlockSpec, EyeCopy, MontageSpec, SessionRecipe
from wl_preproc.synth.session import generate_session
from wl_preproc.synth.spikeglx import EYE_PROCESSING_MEDIAN_S, NIDQ_SAMPLE_RATE_HZ, SPIKEGLX_PRE_ROLL_S
from wl_preproc.timebase import analog_copy, channels
from wl_preproc.timebase.analog_copy import (
    DECIMATED_RATE_HZ,
    FAMILIES,
    CLOCK_SLACK_S,
    MIN_PAIRS,
    SEARCH_PREFIX_S,
    CopyPlacement,
    CopyRefused,
    NoCopy,
    eye_candidates,
    parse_openiris_stamp,
    place_eye_by_copy,
    read_xa_channels,
)
from wl_preproc.timebase._nidq_meta import read_nidq_meta
from wl_preproc.timebase.clock import nidq_files

#: The eye's clock 40 ppm fast and the NI's 25 ppm slow, as Task 1's own test.
EYE_PPM, NI_PPM = 40.0, -25.0
#: Four XA channels, the copy on the third: fewer than the default sixteen only
#: to keep a 252 s NI file small.
COPY = EyeCopy(channel=2, n_channels=4)
#: The brief's bound on every pair.
PAIR_BOUND_S = 0.5e-3
#: ... and on their mean: the systematic part. A decimated sample stamped at
#: its block's first sample rather than its centre is 14.5 NI samples, 0.48 ms,
#: off, which the bound alone could let through.
MEAN_BOUND_S = 0.1e-3
#: 84 trials of 3 s: 25 windows of `WINDOW_S`, more than `MIN_PAIRS`.
DURATION_S = 252.0


def _recipe(**update) -> SessionRecipe:
    base = {
        **EYE_RECIPE.model_dump(),
        "systems": ("spikeglx", "ohdpi"),
        "blocks": (BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=84, trial_duration_s=3.0),),
        "montages": (MontageSpec(start_s=0.0, end_s=DURATION_S),),
        "eye_copy": COPY,
        "system_drift_ppm": (("ohdpi", EYE_PPM), ("spikeglx", NI_PPM)),
    }
    return SessionRecipe(**{**base, **update})


def _generate(root: Path, recipe: SessionRecipe) -> tuple[Path, Path]:
    """The session's directory and its eye file."""
    generate_session(root, recipe)
    session_dir = SessionLayout(root, SessionId.parse(recipe.session_id)).dir
    (eye_path,) = session_dir.rglob(FILENAME)
    return session_dir, eye_path


@pytest.fixture(scope="module")
def session(tmp_path_factory) -> tuple[Path, Path]:
    return _generate(tmp_path_factory.mktemp("copy"), _recipe())


def _expected_delay_s(copy: EyeCopy) -> float:
    """From a frame's capture to the NI instant its copy's value stands for, by
    the generator's own construction (`synth/spikeglx.py::_eye_copy_analog`):

    - `lag_s`, the DAC's (5 ms);
    - `EYE_PROCESSING_MEDIAN_S`, the tracker's processing, normal, so its mean
      (1.1 ms);
    - half a frame: each frame is held until the next arrives, a zero-order
      hold, whose delay against the frames is half its hold (1 ms at 500 Hz);
    - half an NI sample: a frame is shown from the first sample at or after its
      arrival (`np.ceil`), on average half a sample late (0.017 ms);
    - the moving average's group delay: `np.convolve(..., mode="same")` over
      an even length lags by half a sample (0.017 ms; 0 for an odd length).

    In all 7.133 ms for these numbers.
    """
    length = copy.smoothing_samples
    smoothing_s = ((length - 1) / 2 - (length - 1) // 2) / NIDQ_SAMPLE_RATE_HZ
    return copy.lag_s + EYE_PROCESSING_MEDIAN_S + 0.5 / OHDPI_FPS + 0.5 / NIDQ_SAMPLE_RATE_HZ + smoothing_s


def _truth_ni_s(device_s: np.ndarray, copy: EyeCopy) -> np.ndarray:
    """The NI seconds (from the first NI file's first sample) at which the eye's
    device time `device_s` (seconds from its first frame, its own clock)
    reaches the channel: the frame's session time on the NI's clock, the NI's
    pre-roll, and the copy's delay."""
    session_s = (device_s - OHDPI_PRE_ROLL_S) / (1.0 + EYE_PPM * 1e-6)
    return session_s * (1.0 + NI_PPM * 1e-6) + SPIKEGLX_PRE_ROLL_S + _expected_delay_s(copy)


def _errors_s(placement: CopyPlacement) -> np.ndarray:
    ni_s, device_s = np.array(placement.match.pairs).T
    return ni_s - _truth_ni_s(device_s, COPY)


def test_a_session_with_a_copy_is_placed(session):
    session_dir, eye_path = session
    placement = place_eye_by_copy(session_dir, eye_path)

    assert (placement.match.channel, placement.match.signal) == (COPY.channel, "Left P1-P4 (X, Y)")
    assert placement.match.n_dropped == 0
    assert len(placement.match.pairs) >= max(MIN_PAIRS, 0.9 * placement.match.n_windows)
    errors = _errors_s(placement)
    assert np.max(np.abs(errors)) <= PAIR_BOUND_S
    assert abs(float(np.mean(errors))) <= MEAN_BOUND_S
    # No frame dropped: the eye sample at the first pair is the row of its frame.
    assert placement.first_sample == round(placement.match.pairs[0][1] * OHDPI_FPS)
    # The synthetic name carries no stamp: placed, by a search the clocks did not aim, which says why.
    assert placement.search_unaimed == f"{eye_path.stem!r} is not OpenIris's default name, OpenIris-YYYYMonDD-HHMMSS"

    # The families, in `FAMILIES`' order, from the file's own columns.
    eye_time_s, candidates = eye_candidates(eye_path)
    assert tuple(candidates) == tuple(FAMILIES)
    assert [name.split()[0] for name in candidates] == ["Left"] * 3 + ["Right"] * 3
    columns = read_columns(eye_path, ["LeftCR1X", "LeftCR4X", "LeftCR1Y", "LeftCR4Y", "RightPupilWidth"])
    left = candidates["Left P1-P4 (X, Y)"]
    assert left.shape == (eye_time_s.size, 2)
    assert np.array_equal(left[:, 0], columns["LeftCR1X"] - columns["LeftCR4X"])
    assert np.array_equal(left[:, 1], columns["LeftCR1Y"] - columns["LeftCR4Y"])
    assert np.array_equal(candidates["Right PupilWidth"][:, 0], columns["RightPupilWidth"])
    assert np.allclose(eye_time_s, np.arange(eye_time_s.size) / OHDPI_FPS, rtol=0.0, atol=1e-9)


def test_a_dropped_frame_does_not_shift_later_pairs(tmp_path):
    """Two runs of dropped frames, 10 before the first window's centre and 25
    in mid-session. Device time from frame numbers keeps every pair on the
    truth; from rows, every pair after a gap would be 20 or 70 ms early."""
    plain = _recipe()
    n_frames = frame_count(plain)
    early = drop_ohdpi_frames(n_frames, at_s=2.0, n_frames=10)
    middle = drop_ohdpi_frames(n_frames, at_s=120.0, n_frames=25)
    session_dir, eye_path = _generate(tmp_path, _recipe(ohdpi_dropped_frames=early + middle))

    placement = place_eye_by_copy(session_dir, eye_path)

    ni_s, device_s = np.array(placement.match.pairs).T
    assert np.count_nonzero(device_s > 120.0 + 25 / OHDPI_FPS) >= 10
    errors = _errors_s(placement)
    assert np.max(np.abs(errors)) <= PAIR_BOUND_S
    assert abs(float(np.mean(errors))) <= MEAN_BOUND_S
    # The first pair's frame, less the 10 rows dropped before it.
    assert placement.first_sample == round(device_s[0] * OHDPI_FPS) - len(early)


def test_the_channel_search_reads_only_its_prefix(session, monkeypatch):
    """The search reads every XA channel over `SEARCH_PREFIX_S` and
    `CLOCK_SLACK_S` from the eye file's start, here taken as the NI's first
    sample (the synthetic name carries no stamp, so the search is not aimed),
    and only the chosen channel is read in full. And `read_xa_channels` reads
    no NI row past its `stop_s`, nor before its `from_s`."""
    session_dir, eye_path = session
    calls = _record_reads(monkeypatch)
    place_eye_by_copy(session_dir, eye_path)
    assert calls == [(SEARCH_PREFIX_S + CLOCK_SLACK_S, 0.0, COPY.n_channels), (None, 0.0, 1)]
    monkeypatch.undo()

    ni_path = nidq_files(session_dir)[0]
    rows: list[int] = []
    original_chunks = channels.column_chunks

    def counting(*args, **kwargs):
        for chunk in original_chunks(*args, **kwargs):
            rows.append(len(chunk))
            yield chunk

    monkeypatch.setattr(channels, "column_chunks", counting)
    stop_s = 50.0
    start_s, prefix = read_xa_channels(ni_path, stop_s)
    assert len(prefix) == COPY.n_channels
    assert sum(rows) <= COPY.n_channels * stop_s * NIDQ_SAMPLE_RATE_HZ
    assert all(channel.dtype == np.float32 and channel.size == stop_s * DECIMATED_RATE_HZ for channel in prefix)

    # Each decimated sample is its block's mean, stamped at the block's centre:
    # 14.5 NI samples after its first.
    block = round(NIDQ_SAMPLE_RATE_HZ / DECIMATED_RATE_HZ)
    assert start_s == pytest.approx((block - 1) / 2 / NIDQ_SAMPLE_RATE_HZ, abs=1e-12)
    words = np.fromfile(ni_path, dtype=np.int16).reshape(-1, COPY.n_channels + 2)
    usable = int(stop_s * NIDQ_SAMPLE_RATE_HZ)
    for k, channel in enumerate(prefix):
        expected = words[:usable, k].astype(np.float64).reshape(-1, block).mean(axis=1)
        assert np.allclose(channel, expected, rtol=1e-6, atol=1e-3), k

    rows.clear()
    _, (whole,) = read_xa_channels(ni_path, None, channels=[COPY.channel])
    assert whole.size == len(words) // block
    assert sum(rows) == whole.size * block

    # From a bound: the first whole block at or after it, on the same grid of
    # blocks as from the file's start, stamped at its true sample's centre.
    rows.clear()
    from_s = 10.0005
    first_block = 10_001  # ceil(10.0005 s * 30000 / 30)
    later_s, later = read_xa_channels(ni_path, stop_s, from_s=from_s)
    assert later_s == pytest.approx(start_s + first_block / DECIMATED_RATE_HZ, abs=1e-12)
    for k, channel in enumerate(later):
        assert np.array_equal(channel, prefix[k][first_block:]), k
    assert sum(rows) == COPY.n_channels * (stop_s * DECIMATED_RATE_HZ - first_block) * block


def test_the_xa_channels_follow_mn_and_ma_at_the_files_own_rate(tmp_path):
    """A hand-written NI file with two multiplexed aux channels (MA) before
    three XA channels: the XA channels are the census's last analog ones. Its
    calibrated rate, 29999.4 Hz, is not a whole multiple of
    `DECIMATED_RATE_HZ`: blocks of 30 samples (the nearest whole number), at
    29999.4 / 30 Hz, each stamped at its centre on the file's own rate."""
    rate_hz, n_ma, n_xa, n_rows = 29999.4, 2, 3, 3000
    words = np.zeros((n_rows, n_ma + n_xa + 1), dtype=np.int16)
    words[:, :n_ma] = -7
    for k in range(n_xa):
        words[:, n_ma + k] = 100 * (k + 1) + np.arange(n_rows) % 30
    ni_path = tmp_path / "run_g0_t0.nidq.bin"
    words.tofile(ni_path)
    ni_path.with_suffix(".meta").write_text(
        f"niSampRate={rate_hz}\nsnsMnMaXaDw=0,{n_ma},{n_xa},1\n", encoding="utf-8"
    )

    assert analog_copy._decimation(rate_hz) == (30, pytest.approx(rate_hz / 30, rel=1e-15))
    start_s, read = read_xa_channels(ni_path, None)
    assert start_s == pytest.approx(14.5 / rate_hz, rel=1e-12)
    assert len(read) == n_xa
    for k, channel in enumerate(read):
        # Each block of 30 holds 0..29 above its channel's base: a mean of 14.5.
        assert np.array_equal(channel, np.full(n_rows // 30, 100 * (k + 1) + 14.5, dtype=np.float32)), k
    _, (only,) = read_xa_channels(ni_path, None, channels=[2])
    assert np.array_equal(only, read[2])


def test_a_session_without_xa_channels_is_no_copy(tmp_path, caplog):
    """The NI recorded no non-multiplexed analog channel: no copy, said as a
    WARNING naming the eye file."""
    recipe = _recipe(
        eye_copy=None,
        blocks=EYE_RECIPE.blocks,
        montages=EYE_RECIPE.montages,
    )
    session_dir, eye_path = _generate(tmp_path, recipe)

    with caplog.at_level(logging.WARNING, logger=analog_copy.__name__), pytest.raises(NoCopy):
        place_eye_by_copy(session_dir, eye_path)
    assert any(
        record.levelno == logging.WARNING and str(eye_path) in record.getMessage() for record in caplog.records
    )


def _warned_about(caplog, eye_path: Path) -> bool:
    return any(record.levelno == logging.WARNING and str(eye_path) in record.getMessage() for record in caplog.records)


def test_a_stalled_copy_is_refused_with_a_warning(tmp_path, caplog):
    """A stall of 50 s, 20 ms late, mid-session: the copy is found, but its
    pairs do not fit within `MAX_DROPPED_FRACTION`. `CopyRefused` (failed), said
    as a WARNING naming the eye file, as a `NoCopy` is."""
    stalled = EyeCopy(channel=COPY.channel, n_channels=COPY.n_channels, stall=(100.0, 150.0, 0.02))
    session_dir, eye_path = _generate(tmp_path, _recipe(eye_copy=stalled))

    with caplog.at_level(logging.WARNING, logger=analog_copy.__name__), pytest.raises(CopyRefused) as refused:
        place_eye_by_copy(session_dir, eye_path)
    assert _warned_about(caplog, eye_path)
    # Named by its channel and signal, as a match too short whole is.
    assert str(refused.value).startswith(f"channel XA{COPY.channel}'s 'Left P1-P4 (X, Y)': "), refused.value
    # ... and carrying why its search was not aimed, for the `Copy` row.
    assert "is not OpenIris's default name" in refused.value.search_unaimed


def test_too_few_windows_over_the_whole_channel_is_a_refusal(session, monkeypatch, caplog):
    """`find_channel` found `MIN_PAIRS` distinct windows on the chosen channel's
    prefix, so a copy is there: a `NoCopy` from matching that channel whole is
    a failure (`CopyRefused`, amendment 77), not an absence, and names both."""
    session_dir, eye_path = session

    def too_few(*args, **kwargs):
        raise NoCopy("3 of 25 windows matched the channel distinctly, and a copy needs 20")

    monkeypatch.setattr(analog_copy, "match_copy", too_few)
    with caplog.at_level(logging.WARNING, logger=analog_copy.__name__), pytest.raises(CopyRefused) as refused:
        place_eye_by_copy(session_dir, eye_path)
    message = str(refused.value)
    assert f"XA{COPY.channel}" in message and "Left P1-P4 (X, Y)" in message and "3 of 25 windows" in message
    assert isinstance(refused.value.__cause__, NoCopy)
    assert _warned_about(caplog, eye_path)


# ---------------------------------------------------------------------------
# The search aimed by the two files' clocks (amendment 85): OpenIris's
# file-name stamp against SpikeGLX's `fileCreateTime`.
#
# A synthetic eye file starts 0.1 s after its NI file, and a recipe cannot move
# it, so the gap is made here on a copy of the module's session: the eye file
# loses its first rows (the eye started later), or the NI file loses its first
# samples (the NI started later). Every recipe's bytes stay as they are. With
# the prefix at 60 s and the slack at 10 s, a 100 s gap is beyond what an
# unaimed search can bridge in a 252 s session; `MIN_PAIRS` at 5, since a 60 s
# prefix holds 6 windows.

#: The gap made between the two files' starts.
GAP_S = 100.0


@pytest.fixture
def small_search(monkeypatch):
    monkeypatch.setattr(analog_copy, "SEARCH_PREFIX_S", 60.0)
    monkeypatch.setattr(analog_copy, "CLOCK_SLACK_S", 10.0)
    monkeypatch.setattr(analog_copy, "MIN_PAIRS", 5)


def _copied(session, root: Path) -> tuple[Path, Path]:
    session_dir, eye_path = session
    copy_dir = root / "session"
    shutil.copytree(session_dir, copy_dir)
    return copy_dir, copy_dir / eye_path.relative_to(session_dir)


def _ni_created(session_dir: Path) -> datetime.datetime:
    created = read_nidq_meta(nidq_files(session_dir)[0].with_suffix(".meta")).file_created
    assert created is not None
    return created


def _stamped(eye_path: Path, wall: datetime.datetime) -> Path:
    """The eye file renamed as OpenIris names it, `OpenIris-YYYYMonDD-HHMMSS`."""
    stamped = eye_path.with_name(f"OpenIris-{wall:%Y}{_MONTHS[wall.month - 1]}{wall:%d-%H%M%S}.txt")
    eye_path.rename(stamped)
    return stamped


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _eye_starts_late(session, root: Path) -> tuple[Path, Path, float, int]:
    """The module's session with the eye file's first `GAP_S` of rows gone:
    the session's directory, its eye file (unstamped), the eye's true start on
    the NI, in NI seconds, and the rows dropped."""
    session_dir, eye_path = _copied(session, root)
    dropped = int(GAP_S * OHDPI_FPS)
    lines = eye_path.read_text(encoding="utf-8").splitlines(keepends=True)
    eye_path.write_text(lines[0] + "".join(lines[1 + dropped :]), encoding="utf-8")
    eye_start_s = float(_truth_ni_s(np.array([dropped / OHDPI_FPS]), COPY)[0]) - _expected_delay_s(COPY)
    return session_dir, eye_path, eye_start_s, dropped


def _ni_starts_late(session, root: Path) -> tuple[Path, Path, float, int]:
    """The module's session with the NI file's first `GAP_S` of samples gone
    (its `fileCreateTime` left as it was, so the stamps are made against it):
    the session's directory, its eye file (unstamped), the eye's true start on
    the shortened NI, in NI seconds (negative), and the samples dropped."""
    session_dir, eye_path = _copied(session, root)
    ni_path = nidq_files(session_dir)[0]
    dropped = int(GAP_S * NIDQ_SAMPLE_RATE_HZ)
    words = np.fromfile(ni_path, dtype=np.int16).reshape(-1, COPY.n_channels + 2)
    words[dropped:].tofile(ni_path)
    eye_start_s = float(_truth_ni_s(np.array([0.0]), COPY)[0]) - _expected_delay_s(COPY) - dropped / NIDQ_SAMPLE_RATE_HZ
    return session_dir, eye_path, eye_start_s, dropped


def _record_reads(monkeypatch) -> list[tuple[float | None, float, int]]:
    """`read_xa_channels`' calls, as `(stop_s, from_s, channels read)`."""
    calls: list[tuple[float | None, float, int]] = []
    original = analog_copy.read_xa_channels

    def recording(ni_path, stop_s, channels=None, from_s=0.0):
        start_s, read = original(ni_path, stop_s, channels, from_s=from_s)
        calls.append((stop_s, from_s, len(read)))
        return start_s, read

    monkeypatch.setattr(analog_copy, "read_xa_channels", recording)
    return calls


def test_an_eye_file_started_well_after_the_ni_is_found_by_its_stamp(session, tmp_path, small_search):
    session_dir, eye_path, eye_start_s, dropped = _eye_starts_late(session, tmp_path)
    eye_path = _stamped(eye_path, _ni_created(session_dir) + datetime.timedelta(seconds=math.floor(eye_start_s)))

    placement = place_eye_by_copy(session_dir, eye_path)

    assert (placement.match.channel, placement.match.signal) == (COPY.channel, "Left P1-P4 (X, Y)")
    ni_s, device_s = np.array(placement.match.pairs).T
    # The eye's own seconds now start `dropped` frames later.
    errors = ni_s - _truth_ni_s(device_s + dropped / OHDPI_FPS, COPY)
    assert np.max(np.abs(errors)) <= PAIR_BOUND_S
    assert abs(float(np.mean(errors))) <= MEAN_BOUND_S
    assert placement.search_unaimed is None


def test_an_eye_file_started_well_before_the_ni_is_found_by_its_stamp(session, tmp_path, small_search):
    session_dir, eye_path, eye_start_s, dropped = _ni_starts_late(session, tmp_path)
    eye_path = _stamped(eye_path, _ni_created(session_dir) + datetime.timedelta(seconds=math.floor(eye_start_s)))

    placement = place_eye_by_copy(session_dir, eye_path)

    assert (placement.match.channel, placement.match.signal) == (COPY.channel, "Left P1-P4 (X, Y)")
    ni_s, device_s = np.array(placement.match.pairs).T
    # The NI's own seconds now start `dropped` samples later.
    errors = ni_s + dropped / NIDQ_SAMPLE_RATE_HZ - _truth_ni_s(device_s, COPY)
    assert np.max(np.abs(errors)) <= PAIR_BOUND_S
    assert abs(float(np.mean(errors))) <= MEAN_BOUND_S
    assert placement.search_unaimed is None


@pytest.mark.parametrize("missing", ["stamp", "fileCreateTime"])
def test_without_both_clocks_the_search_is_not_aimed(session, tmp_path, small_search, caplog, missing):
    """The same late eye file, its name unstamped or its NI meta without
    `fileCreateTime`: the search is not aimed, so the copy is beyond it
    (`NoCopy`). A WARNING names the eye file and why, since nothing in
    `wl_preproc` configures logging and an INFO line would never be seen; and
    the `NoCopy` carries why, for the `Copy` row (`search_unaimed`)."""
    session_dir, eye_path, eye_start_s, _ = _eye_starts_late(session, tmp_path)
    if missing == "fileCreateTime":
        eye_path = _stamped(eye_path, _ni_created(session_dir) + datetime.timedelta(seconds=math.floor(eye_start_s)))
        meta = nidq_files(session_dir)[0].with_suffix(".meta")
        kept = [line for line in meta.read_text(encoding="utf-8").splitlines() if not line.startswith("fileCreateTime=")]
        meta.write_text("\n".join(kept) + "\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=analog_copy.__name__), pytest.raises(NoCopy) as absent:
        place_eye_by_copy(session_dir, eye_path)
    unaimed = [r.getMessage() for r in caplog.records
               if r.levelno == logging.WARNING and str(eye_path) in r.getMessage() and "not aimed" in r.getMessage()]
    assert len(unaimed) == 1
    why = "fileCreateTime" if missing == "fileCreateTime" else "OpenIris"
    assert why in unaimed[0]
    assert why in absent.value.search_unaimed and absent.value.search_unaimed in unaimed[0]


def test_the_aimed_search_reads_from_the_aimed_ni_start(session, tmp_path, small_search, monkeypatch):
    """The eye file stamped `d` seconds after the NI's creation: every XA
    channel is read over `[d - CLOCK_SLACK_S, d + SEARCH_PREFIX_S +
    CLOCK_SLACK_S]`, not from 0; then the chosen channel whole."""
    session_dir, eye_path, eye_start_s, _ = _eye_starts_late(session, tmp_path)
    d = float(math.floor(eye_start_s))
    eye_path = _stamped(eye_path, _ni_created(session_dir) + datetime.timedelta(seconds=d))
    calls = _record_reads(monkeypatch)

    place_eye_by_copy(session_dir, eye_path)

    (prefix_stop, prefix_from, n_read), whole = calls
    assert prefix_from == pytest.approx(d - 10.0)
    assert prefix_stop == pytest.approx(d + 60.0 + 10.0)
    assert n_read == COPY.n_channels
    assert whole == (None, 0.0, 1)


def test_the_openiris_stamp_is_parsed_by_its_own_month_names():
    assert parse_openiris_stamp("OpenIris-2024Jul31-114628") == datetime.datetime(2024, 7, 31, 11, 46, 28)
    assert parse_openiris_stamp("OpenIris-2024JUL31-114628") == datetime.datetime(2024, 7, 31, 11, 46, 28)
    assert parse_openiris_stamp("OpenIris-2026dec01-000000") == datetime.datetime(2026, 12, 1, 0, 0, 0)
    for name in ("OpenIris-synthetic", "OpenIris-2024Foo31-114628", "OpenIris-2024Feb30-114628",
                 "OpenIris-2024Jul31-256628", "recording-2024Jul31-114628"):
        with pytest.raises(ValueError):
            parse_openiris_stamp(name)


def test_the_clock_offset_reads_both_clocks_in_one_zone(monkeypatch):
    """Both stamps are lab PCs' local time: subtracted as they are, or across a
    change to daylight-saving time in `clock.NIDQ_TIMEZONE` where it is set.
    One aware and one naive, the naive one is UTC, as `clock.aware_utc` has it."""
    from wl_preproc.timebase import clock

    eye_path = Path("OpenIris-2026Mar08-030100.txt")
    ni_created = datetime.datetime(2026, 3, 8, 1, 59, 0)
    monkeypatch.setattr(clock, "NIDQ_TIMEZONE", None)
    assert analog_copy.clock_offset_s(eye_path, ni_created) == (pytest.approx(62 * 60.0), None)
    # New York springs from 02:00 to 03:00 that night: two minutes apart.
    monkeypatch.setattr(clock, "NIDQ_TIMEZONE", "America/New_York")
    assert analog_copy.clock_offset_s(eye_path, ni_created) == (pytest.approx(120.0), None)
    # The eye's 03:01:00 read as UTC, whatever the zone: 90 s after 07:59:30
    # at UTC+5, which is 02:59:30 UTC.
    aware = datetime.datetime(2026, 3, 8, 7, 59, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=5)))
    assert analog_copy.clock_offset_s(eye_path, aware) == (pytest.approx(90.0), None)


def test_without_both_clocks_the_offset_is_0_and_says_why(caplog):
    """Either clock missing: `d` is 0, with the reason returned and logged as
    one WARNING naming the eye file."""
    ni_created = datetime.datetime(2026, 3, 8, 1, 59, 0)
    unstamped = Path("renamed-by-hand.txt")
    with caplog.at_level(logging.WARNING, logger=analog_copy.__name__):
        offset_s, reason = analog_copy.clock_offset_s(unstamped, ni_created)
    assert (offset_s, reason) == (0.0, "'renamed-by-hand' is not OpenIris's default name, OpenIris-YYYYMonDD-HHMMSS")
    (record,) = caplog.records
    assert record.levelno == logging.WARNING and str(unstamped) in record.getMessage() and reason in record.getMessage()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=analog_copy.__name__):
        assert analog_copy.clock_offset_s(Path("OpenIris-2026Mar08-030100.txt"), None) == (
            0.0, "the NI file's .meta has no fileCreateTime")
    (record,) = caplog.records
    assert record.levelno == logging.WARNING and "fileCreateTime" in record.getMessage()

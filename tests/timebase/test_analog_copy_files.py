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

import logging
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
    MIN_PAIRS,
    SEARCH_PREFIX_S,
    CopyPlacement,
    NoCopy,
    eye_candidates,
    place_eye_by_copy,
    read_xa_channels,
)
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
    """The search reads every XA channel to `SEARCH_PREFIX_S` from the eye
    file's start (taken as the NI's first sample: nothing places the eye on the
    NI before the copy does), and only the chosen channel is read in full. And
    `read_xa_channels` reads no NI row past its `stop_s`."""
    session_dir, eye_path = session
    calls: list[tuple[float | None, int]] = []
    original = analog_copy.read_xa_channels

    def recording(ni_path, stop_s, *args, **kwargs):
        start_s, read = original(ni_path, stop_s, *args, **kwargs)
        calls.append((stop_s, len(read)))
        return start_s, read

    monkeypatch.setattr(analog_copy, "read_xa_channels", recording)
    place_eye_by_copy(session_dir, eye_path)
    assert calls == [(SEARCH_PREFIX_S, COPY.n_channels), (None, 1)]
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

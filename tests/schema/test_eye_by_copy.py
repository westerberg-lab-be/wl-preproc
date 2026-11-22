"""The eye tracker placed by its analog copy on the NI (design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendments 77-85).

Under the NI's clock the eye file is matched to its copy on an NI analog
channel (`timebase/analog_copy.py::place_eye_by_copy`), and the pairs are
fitted as Intan's strobes are: `SystemTimebase` fits the rate, with the copy's
lag removed, and `Segment` the offset. Where barcodes also place the file over
the covered span (a partial sync box), the two placements measure the lag;
elsewhere it is carried from the rig's last good measurement, or is 0 and
unmeasured. Every session here is generated, hand-landed and taken through
the daemon, in `test_partial_sync_box.py`'s shape, and is 252 s long: the copy
needs `MIN_PAIRS` windows of `WINDOW_S`.

The truth is the generator's own: an eye frame's NI time from the recipe's
drifts and pre-rolls, and the copy's delay from the generator's constants
(`tests/timebase/test_analog_copy_files.py::_expected_delay_s`, about 7.13 ms
for a 5 ms `lag_s`, ruling P1), never read back from the code under test.
"""

from __future__ import annotations

import collections
import re
from types import SimpleNamespace

import pytest

from tests.identities import new_animal, new_serial
from tests.schema.test_ni_clock import _errors_of

#: The eye's clock 30 ppm fast, and the NI's 25 ppm slow, so a time on either
#: differs from the truth's by more than the bounds below within the session.
DRIFT_PPM = (("ohdpi", 30.0), ("spikeglx", -25.0))
#: 84 trials of 3 s: 25 windows of `WINDOW_S`, more than `MIN_PAIRS`.
N_TRIALS, TRIAL_S = 84, 3.0
#: The box stops inside the first trial window's stretch, as a Pi that lost
#: power: its barcodes cover about 0.7 to 8.7 s of NI time, which holds one of
#: the copy's pairs, the first window's centre.
STOP_S = 8.7
#: Four XA channels, the copy on the third (Task 3's `COPY`): fewer than the
#: default sixteen only to keep each NI file small.
COPY_CHANNEL, N_XA = 2, 4
#: The stalls, session seconds `(start, stop, extra lag)`: over about 25% of
#: the file, too many windows off the line to drop (refused), and over 5%,
#: few enough (placed, the stretch dropped).
LONG_STALL = (100.0, 163.0, 0.02)
SHORT_STALL = (100.0, 112.6, 0.02)
#: `copy_disagree`'s copy runs on a clock 3 ms/s off the eye's, about the NI
#: instant `DISAGREE_PIVOT_S`, the span's one pair: there the lag is the
#: generator's, and at the span's ends the corrected copy is about 13 ms off
#: the barcodes, far past a frame.
DISAGREE_PPM = 3000.0
DISAGREE_PIVOT_S = 5.1
#: `copy_negative`: the recipe's `lag_s` cannot be negative (`EyeCopy`), so
#: its copy is generated with `lag_s=0` and moved this much earlier, as a
#: `lag_s` of -4 ms would place it: about -1.9 ms after the generator's own
#: delay, a copy leading its frame.
NEGATIVE_SHIFT_S = 0.004
#: `copy_earlier`'s and `copy_later`'s copies: late by lags of their own,
#: each more than a millisecond from `copy_partial`'s 5 ms.
EARLIER_LAG_S, LATER_LAG_S = 0.008, 0.011
#: Where `copy_two_files`' eye file is cut in two, eye seconds.
SPLIT_S = 126.0
#: The bound on a lag the box measured (ruling P1), and on a placement whose
#: lag is right (measured here, or carried).
#: The sessions that hold calibration windows and fixations: the one a copy
#: places beyond its box's span, and the one whose copy is absent, which the
#: span still cuts.
WINDOWED = ("copy_partial", "copy_absent")
#: `copy_partial`'s ten calibration targets, in pixels at `CAL_SCALE` degrees
#: each, none repeated, held in windows from `WINDOWS_FROM_S` every 1.5 s, all
#: after the box's span. A held gaze is smooth, so its windows match their copy
#: over a wide peak and the matcher rightly gives them no pair
#: (`timebase/analog_copy.py`): the holds start after the first window of
#: `WINDOW_S`, which holds the span's one pair, and a repeating gaze would
#: match a period off as well.
WINDOWS_FROM_S = 10.1
HOLDS_PX = ((0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0), (250.0, 250.0),
            (-300.0, 200.0), (150.0, -400.0), (-450.0, -250.0), (350.0, 300.0), (-100.0, 450.0))


def _window_starts_s() -> tuple:
    return tuple(WINDOWS_FROM_S + 1.5 * index for index in range(len(HOLDS_PX)))


def _fixations() -> tuple:
    """`copy_partial`'s gaze: held at `HOLDS_PX` in turn, one hold per
    calibration window (`_window_starts_s`), joined by ramps, from the first
    window's start; the generator's saccades before and after."""
    from wl_preproc.synth.recipe import EyeFixationSpec

    from tests.schema.test_detect_populate import _ramp_fixations
    from tests.schema.test_partial_sync_box import _RAMP_AFTER_S, _RAMP_S, _RAMP_STEPS

    starts = _window_starts_s()
    fixations, held_from_s = [], starts[0]
    for index, start_s in enumerate(starts):
        here = HOLDS_PX[index]
        if index == len(starts) - 1:
            fixations.append(EyeFixationSpec(start_s=held_from_s, end_s=start_s + 1.4, x_px=here[0], y_px=here[1]))
            break
        ramp_s = start_s + _RAMP_AFTER_S
        fixations.append(EyeFixationSpec(start_s=held_from_s, end_s=ramp_s, x_px=here[0], y_px=here[1]))
        fixations.extend(_ramp_fixations(ramp_s, here, HOLDS_PX[index + 1], _RAMP_S, _RAMP_STEPS))
        held_from_s = ramp_s + _RAMP_S
    return tuple(fixations)


def _write_windows(session_dir, recipe, truth) -> None:
    """The session's code words with a calibration window at each hold
    (`test_partial_sync_box.py::_write_calibration_windows`, at `_window_starts_s`),
    in the NI's stream, which regenerates its analog channels as the recipe
    says, and the box's log."""
    import dataclasses

    from wl_preproc.synth.spikeglx import write_nidq
    from wl_preproc.synth.syncbox import write_syncbox_log

    from tests.schema.test_eye_populate import _fixation_words
    from tests.schema.test_partial_sync_box import CAL_SCALE

    extra = []
    for start_s, (x_px, y_px) in zip(_window_starts_s(), HOLDS_PX, strict=True):
        extra.extend(_fixation_words(start_s, (CAL_SCALE * x_px, CAL_SCALE * y_px)))
    words = tuple(sorted((*truth.code_words, *extra), key=lambda pair: pair[0]))
    written = dataclasses.replace(truth, code_words=words)
    write_syncbox_log(session_dir / "syncbox" / "seg-000.log", recipe, written)
    write_nidq(session_dir / "spikeglx", recipe, written, drift_ppm=dict(recipe.system_drift_ppm)["spikeglx"])
LAG_BOUND_S = 0.5e-3
PLACED_BOUND_S = 1e-3


class _Landed(dict):
    reports: list
    #: How often each eye file was matched to its copy over both passes.
    placements: collections.Counter

    def key(self, name):
        return self[name][2]

    def recipe(self, name):
        return self[name][0]

    def session_dir(self, name):
        return self[name][3]


def _ni_samples(session_dir):
    """The first NI file's samples, `(n_samples, n_channels)`, writable."""
    import numpy as np

    from wl_preproc.timebase._nidq_meta import read_nidq_meta
    from wl_preproc.timebase.clock import nidq_files

    path = nidq_files(session_dir)[0]
    meta = read_nidq_meta(path.with_suffix(".meta"))
    return np.memmap(path, dtype=np.int16, mode="r+").reshape(-1, meta.n_channels)


def _shift_the_copy_earlier(session_dir, shift_s: float) -> None:
    """The copy's channel moved `shift_s` earlier on the NI, its last value
    held to the end: a copy that leads its frame by that much more."""
    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ

    samples = _ni_samples(session_dir)
    shift = round(shift_s * NIDQ_SAMPLE_RATE_HZ)
    column = samples[:, COPY_CHANNEL].copy()
    samples[:-shift, COPY_CHANNEL] = column[shift:]
    samples[-shift:, COPY_CHANNEL] = column[-1]
    samples.flush()


def _warp_the_copy(session_dir, ppm: float, pivot_s: float) -> None:
    """The copy's channel as one written from a clock `ppm` slow about the NI
    instant `pivot_s` (seconds from its first sample): sample `k` shows what
    sample `pivot + (k - pivot) * (1 - ppm / 1e6)` held, the nearest."""
    import numpy as np

    from wl_preproc.synth.spikeglx import NIDQ_SAMPLE_RATE_HZ

    samples = _ni_samples(session_dir)
    column = samples[:, COPY_CHANNEL].copy()
    pivot = round(pivot_s * NIDQ_SAMPLE_RATE_HZ)
    shown = pivot + (np.arange(column.size) - pivot) * (1.0 - ppm * 1e-6)
    samples[:, COPY_CHANNEL] = column[np.clip(np.rint(shown), 0, column.size - 1).astype(np.int64)]
    samples.flush()


def _split_the_eye_file(session_dir, at_s: float) -> None:
    """The eye file cut in two at eye second `at_s`, each half with the
    header, as OpenIris restarted would leave it: `OpenIris-synthetic.txt`
    and `OpenIris-synthetic-2.txt`."""
    from wl_preproc.synth.ohdpi import FILENAME, OHDPI_FPS

    path = session_dir / "ohdpi" / FILENAME
    header, *rows = path.read_text(encoding="utf-8").splitlines()
    cut = round(at_s * OHDPI_FPS)
    path.write_text("\n".join([header, *rows[:cut]]) + "\n", encoding="utf-8")
    path.with_name(path.stem + "-2.txt").write_text("\n".join([header, *rows[cut:]]) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Each session by name, `(recipe, truth, key, session_dir)`. Every one is
    the NI with the eye tracker, 252 s, its copy on `XA2` of 4 unless it says
    otherwise, on a rig of its own unless it says otherwise:

    - `copy_only`: no box;
    - `copy_partial`: the box stopping at `STOP_S`;
    - `copy_carried`: no box, on `copy_partial`'s rig an hour later;
    - `copy_earlier` and `copy_later`: the box stopping at `STOP_S`, on that
      rig an hour before `copy_partial` and an hour after `copy_carried`,
      their copies `EARLIER_LAG_S` and `LATER_LAG_S` late, so each measures a
      lag of its own;
    - `copy_absent`: the box stopping at `STOP_S`, the NI's copy channel
      carrying noise alone, and `copy_absent_plain`, the same recipe with no
      copy at all (3b-3's partial);
    - `copy_partial` and `copy_absent` also hold ten calibration windows and
      fixations in the eye's gaze (`WINDOWED`): `copy_partial`'s all after the
      box's span, `copy_absent`'s five inside it and five after it
      (amendment 81);
    - `copy_noise` and `copy_flat`: no box, the channel noise alone or flat;
    - `copy_stall`: no box, a stall over 25% of the file (`LONG_STALL`);
      `copy_stall_box` the same with the box stopping at `STOP_S`;
    - `copy_short_stall`: no box, a stall over 5% of it (`SHORT_STALL`);
    - `copy_disagree`: the box stopping at `STOP_S`, the copy on a clock
      3 ms/s off (`_warp_the_copy`);
    - `copy_negative`: the same box, the copy leading its frame
      (`_shift_the_copy_earlier`), on `copy_disagree`'s rig an hour after it;
    - `copy_after_bad`: no box, on that rig an hour after `copy_negative`;
    - `copy_two_files`: the box stopping at `STOP_S`, the eye file cut in two
      (`_split_the_eye_file`).

    Two daemon passes (ruling P3: a lag is carried from what exists when a
    session is placed): `copy_carried` and `copy_after_bad` land after the
    first, which places the sessions they would carry from, and `copy_later`
    too, so a lag measured after a session is there to be wrongly carried.
    Deleted afterwards."""
    from wl_preproc import daemon
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import NI_CLOCK_RECIPE, BlockSpec, EyeCopy, MontageSpec
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase import analog_copy

    from tests.schema.test_eye_populate import _land
    from tests.schema.test_partial_sync_box import _eye_fixations, _write_calibration_windows

    daemon.activate_all(prefix=prefix)
    serial = new_serial()
    sessions = _Landed()
    copy = EyeCopy(channel=COPY_CHANNEL, n_channels=N_XA)
    carry, bad = new_animal(), new_animal()
    hour_later = {}

    def at(animal):
        """The animal's next session, an hour after its last, on its first day."""
        session = animal.session(on=hour_later.get(animal.subject))
        hour_later[animal.subject] = session.date
        return session

    def land(name: str, *, box: bool, session=None, rig: str | None = None, **update):
        if session is None:
            session = new_animal().session()
        recipe = NI_CLOCK_RECIPE.model_copy(update={
            "subject": session.subject, "session_id": session.session_id, "probe_serial": serial,
            "rig": rig or f"rig-{name}",
            "systems": ("syncbox", "spikeglx", "ohdpi") if box else ("spikeglx", "ohdpi"),
            "blocks": (BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=N_TRIALS, trial_duration_s=TRIAL_S),),
            "montages": (MontageSpec(start_s=0.0, end_s=N_TRIALS * TRIAL_S),),
            "eye_copy": copy, "system_drift_ppm": DRIFT_PPM,
            **({"syncbox_stop_s": STOP_S} if box else {}),
            **({"eye_fixations": _fixations()} if name == "copy_partial" else {}),
            **({"eye_fixations": _eye_fixations()} if name == "copy_absent" else {}), **update})
        root = tmp_path_factory.mktemp(name)
        truth = generate_session(root, recipe)
        session_dir = root / recipe.session_id
        if name == "copy_disagree":
            _warp_the_copy(session_dir, DISAGREE_PPM, DISAGREE_PIVOT_S)
        if name == "copy_negative":
            _shift_the_copy_earlier(session_dir, NEGATIVE_SHIFT_S)
        if name == "copy_two_files":
            _split_the_eye_file(session_dir, SPLIT_S)
        if name == "copy_partial":
            _write_windows(session_dir, recipe, truth)
        if name == "copy_absent":
            _write_calibration_windows(session_dir, recipe, truth)
        key = _land(root, recipe, session.session_datetime, acquisition_systems=recipe.systems)
        sessions[name] = (recipe, truth, key, session_dir)

    earlier, partial, carried, later = (at(carry) for _ in range(4))
    disagree, negative, after_bad = (at(bad) for _ in range(3))
    land("copy_only", box=False)
    land("copy_earlier", box=True, session=earlier, rig="rig-copy-carry",
         eye_copy=copy.model_copy(update={"lag_s": EARLIER_LAG_S}))
    land("copy_partial", box=True, session=partial, rig="rig-copy-carry")
    land("copy_later", box=True, session=later, rig="rig-copy-carry",
         eye_copy=copy.model_copy(update={"lag_s": LATER_LAG_S}))
    land("copy_absent", box=True, eye_copy=copy.model_copy(update={"source": "noise"}))
    land("copy_absent_plain", box=True, eye_copy=None)
    land("copy_noise", box=False, eye_copy=copy.model_copy(update={"source": "noise"}))
    land("copy_flat", box=False, eye_copy=copy.model_copy(update={"source": "flat"}))
    land("copy_stall", box=False, eye_copy=copy.model_copy(update={"stall": LONG_STALL}))
    land("copy_stall_box", box=True, eye_copy=copy.model_copy(update={"stall": LONG_STALL}))
    land("copy_short_stall", box=False, eye_copy=copy.model_copy(update={"stall": SHORT_STALL}))
    land("copy_disagree", box=True, session=disagree, rig="rig-copy-bad")
    land("copy_negative", box=True, session=negative, rig="rig-copy-bad",
         eye_copy=copy.model_copy(update={"lag_s": 0.0}))
    land("copy_two_files", box=True)
    sessions.placements = collections.Counter()
    place = analog_copy.place_eye_by_copy

    def counted(session_dir, eye_path):
        sessions.placements[eye_path] += 1
        return place(session_dir, eye_path)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(analog_copy, "place_eye_by_copy", counted)
        first = daemon.run_once(prefix=prefix)
        land("copy_carried", box=False, session=carried, rig="rig-copy-carry")
        land("copy_after_bad", box=False, session=after_bad, rig="rig-copy-bad")
        second = daemon.run_once(prefix=prefix)
    sessions.reports = [first, second]
    yield sessions
    for _recipe, _truth, key, _session_dir in sessions.values():
        (pipeline.Session & key).delete(prompt=False)


def _fit(key, system="ohdpi") -> dict:
    from wl_preproc.schema import timebase

    return (timebase.SystemTimebase & key & {"system": system}).fetch1()


def _copy(key) -> dict:
    from wl_preproc.schema import timebase

    return (timebase.SystemTimebase.Copy & key).fetch1()


def _tier(key) -> str:
    from wl_preproc.schema import timebase

    return (timebase.TimingProvenance & key).fetch1("tier")


def _segment(key) -> dict:
    from wl_preproc.schema import core

    return (core.Segment & key & {"system": "ohdpi"}).fetch1()


def _reasons(key) -> list[str]:
    from wl_preproc.schema import core

    return sorted(row["reason"] for row in (core.RejectedSegment & key & {"system": "ohdpi"}).to_dicts())


def _session_s(recipe, device_s: float) -> float:
    """The NI seconds (session time) of the eye's own `device_s`: its frame's
    truth time on the NI's drifting clock, after the NI's pre-roll."""
    from wl_preproc.synth.ohdpi import OHDPI_PRE_ROLL_S
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S

    drift = dict(recipe.system_drift_ppm)
    truth_s = (device_s - OHDPI_PRE_ROLL_S) / (1.0 + drift["ohdpi"] * 1e-6)
    return truth_s * (1.0 + drift["spikeglx"] * 1e-6) + SPIKEGLX_PRE_ROLL_S


def _end_device_s(landed, name) -> float:
    from wl_preproc.timebase import segments

    (scan,) = segments.scan_system("ohdpi", landed.session_dir(name) / "ohdpi")
    return scan.duration_s


def _delay_s(recipe) -> float:
    """The generator's delay from a frame to its copy (ruling P1)."""
    from tests.timebase.test_analog_copy_files import _expected_delay_s

    return _expected_delay_s(recipe.eye_copy)


def test_a_session_with_only_a_copy_is_tier_n(landed):
    """Amendments 77 and 79: no box, so no barcode places the file; the copy
    places all of it, with no lag on the rig to carry: 0 and unmeasured. The
    placement is late by the copy's whole delay, so the segment starts that
    much after the truth (ruling P1: the generator's delay, not the recipe's
    `lag_s` alone)."""
    key = landed.key("copy_only")
    fit = _fit(key)
    assert (fit["fit_status"], fit["time_source"]) == ("fitted", "analog")
    copy = _copy(key)
    assert copy["outcome"] == "placed" and copy["refusal"] == ""
    assert (copy["ni_channel"], copy["signal"]) == (COPY_CHANNEL, "Left P1-P4 (X, Y)")
    assert copy["n_pairs"] >= 20 and copy["n_dropped"] == 0
    assert (copy["lag_s"], bool(copy["lag_measured"]), copy["lag_source_datetime"]) == (0.0, False, None)
    assert copy["agreement_us"] is None
    assert copy["rig"] == "rig-copy_only"
    recipe = landed.recipe("copy_only")
    late_s = _segment(key)["start_s"] - _session_s(recipe, 0.0)
    assert late_s == pytest.approx(_delay_s(recipe), abs=LAG_BOUND_S)
    assert _reasons(key) == []
    assert _tier(key) == "N"


def test_a_partial_box_measures_the_lag_and_places_the_whole_file(landed):
    """Amendment 79: over the covered span both the barcodes and the copy place
    the file, so the lag is measured here, as the generator's delay, and the
    corrected copy agrees with the barcodes within a frame. The whole file is
    placed by the copy: its start and its end, far past the span, on the truth
    to well within a frame."""
    from wl_preproc.schema import timebase

    key = landed.key("copy_partial")
    recipe = landed.recipe("copy_partial")
    fit = _fit(key)
    assert (fit["fit_status"], fit["time_source"]) == ("fitted", "analog")
    copy = _copy(key)
    assert copy["outcome"] == "placed"
    assert bool(copy["lag_measured"]) and copy["lag_source_datetime"] == key["session_datetime"]
    assert copy["lag_s"] == pytest.approx(_delay_s(recipe), abs=LAG_BOUND_S)
    assert 0.0 <= copy["agreement_us"] <= 1e6 / fit["nominal_rate_hz"]
    assert copy["rig"] == "rig-copy-carry"
    segment = _segment(key)
    provenance = (timebase.TimingProvenance & key).fetch1()
    assert segment["start_s"] == pytest.approx(_session_s(recipe, 0.0), abs=PLACED_BOUND_S)
    end_s = _session_s(recipe, _end_device_s(landed, "copy_partial"))
    assert segment["end_s"] == pytest.approx(end_s, abs=PLACED_BOUND_S)
    assert segment["end_s"] > provenance["covered_end_s"] + 200.0
    assert provenance["tier"] == "N", provenance


def test_the_next_session_on_the_rig_carries_the_lag(landed):
    """Amendment 79: a later session on the rig with no box uses the lag the
    rig last measured at or before it, and names the session it came from;
    so it is placed on the truth as the measuring session was. Not the rig's
    earlier lag, nor one measured after it, each far from the right one."""
    key = landed.key("copy_carried")
    source = _copy(landed.key("copy_partial"))
    for other, lag_s in (("copy_earlier", EARLIER_LAG_S), ("copy_later", LATER_LAG_S)):
        measured = _copy(landed.key(other))
        assert bool(measured["lag_measured"]) and measured["lag_source_datetime"] == landed.key(other)["session_datetime"]
        expected_s = _delay_s(landed.recipe(other)) - _delay_s(landed.recipe("copy_partial")) + source["lag_s"]
        assert measured["lag_s"] == pytest.approx(expected_s, abs=LAG_BOUND_S), other
        assert abs(measured["lag_s"] - source["lag_s"]) > 2e-3, other
    copy = _copy(key)
    assert copy["outcome"] == "placed"
    assert bool(copy["lag_measured"]) and copy["agreement_us"] is None
    assert copy["lag_source_datetime"] == landed.key("copy_partial")["session_datetime"]
    assert copy["lag_s"] == source["lag_s"]
    recipe = landed.recipe("copy_carried")
    assert _segment(key)["start_s"] == pytest.approx(_session_s(recipe, 0.0), abs=PLACED_BOUND_S)
    assert _tier(key) == "N"


def test_no_copy_leaves_3b_3_unchanged(landed):
    """Amendment 77: the NI recorded its analog channels, but none carries the
    eye (`copy_absent`), so the copy is absent and 3b-3 stands: the barcodes
    place the file, the session is N, and the span and the eye segment's
    extent are those of the same recipe with no copy at all."""
    from wl_preproc.schema import timebase

    key, plain = landed.key("copy_absent"), landed.key("copy_absent_plain")
    for each in (key, plain):
        copy = _copy(each)
        assert copy["outcome"] == "absent", copy
        assert (copy["ni_channel"], copy["signal"], copy["lag_s"]) == (None, None, None)
        fit = _fit(each)
        assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode")
        assert _tier(each) == "N"
    rows = [(timebase.TimingProvenance & each).fetch1() for each in (key, plain)]
    for field in ("covered_start_s", "covered_end_s"):
        assert rows[0][field] == pytest.approx(rows[1][field], abs=1e-9)
    segments = [_segment(each) for each in (key, plain)]
    for field in ("start_s", "end_s", "offset_s", "first_sample", "n_barcodes"):
        assert segments[0][field] == pytest.approx(segments[1][field], abs=1e-9), field
    assert segments[0]["n_barcodes"] > 0


@pytest.mark.parametrize("name", ["copy_noise", "copy_flat"])
def test_noise_and_flat_channels_are_no_copy(landed, name):
    """Amendment 77: a channel of noise or a flat one gives no distinct
    window, so the copy is absent, and 3b-3 stands: with no box nothing
    places the eye under the NI (3b-1), its file is an unfitted system's, and
    the session is D."""
    key = landed.key(name)
    assert _copy(key)["outcome"] == "absent"
    fit = _fit(key)
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "none")
    assert _reasons(key) == ["unfitted_system"]
    assert _tier(key) == "D"


def test_a_failed_copy_with_no_barcodes_is_no_analog_match(landed):
    """Amendment 78: the copy is found, but a quarter of its pairs lie off the
    line, too many to drop, so it is refused; with no barcode to fall back on
    the file is `no_analog_match`, by the analog mechanism, and the session
    is D."""
    from wl_preproc.synth.ohdpi import OHDPI_PRE_ROLL_S
    from wl_preproc.timebase.analog_copy import WINDOW_S

    key = landed.key("copy_stall")
    copy = _copy(key)
    assert copy["outcome"] == "failed"
    # Named as the stall it is (fix round 1, finding 3): the channel and the
    # signal, about a quarter of the pairs off the line the rest lie on, not
    # all of them, and the stretch they lie in, in the eye's own seconds.
    refusal = copy["refusal"]
    assert refusal.startswith(f"channel XA{COPY_CHANNEL}'s 'Left P1-P4 (X, Y)': "), refusal
    percent = re.search(r"(\d+)% of the pairs", refusal)
    assert percent is not None and 15 <= int(percent.group(1)) <= 35, refusal
    stall_s = [time_s + OHDPI_PRE_ROLL_S for time_s in LONG_STALL[:2]]
    stretches = [(float(start), float(stop)) for start, stop in re.findall(r"(\d+\.\d)-(\d+\.\d) s", refusal)]
    assert any(start <= stall_s[0] + WINDOW_S and stall_s[1] - WINDOW_S <= stop for start, stop in stretches), refusal
    assert copy["lag_s"] is None and copy["n_pairs"] is None
    fit = _fit(key)
    assert (fit["fit_status"], fit["time_source"]) == ("unfittable", "analog")
    assert (fit["fitted_rate_hz"], fit["drift_ppm"]) == (None, None)
    assert _reasons(key) == ["no_analog_match"]
    assert _tier(key) == "D"


def test_a_failed_copy_where_barcodes_place_the_file_keeps_3b_3(landed):
    """The outcome table's failed-with-barcodes row: the refusal is recorded,
    and the file is placed by its barcodes over the span, as 3b-3 places it;
    nothing failed, so the session is N."""
    key = landed.key("copy_stall_box")
    copy = _copy(key)
    assert copy["outcome"] == "failed" and copy["refusal"]
    fit = _fit(key)
    assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode")
    assert _segment(key)["n_barcodes"] > 0
    assert _reasons(key) == []
    assert _tier(key) == "N"


def test_a_short_stall_is_dropped_and_the_file_placed(landed):
    """Amendment 78's drop and refit: a stall over a twentieth of the file
    puts a window or two off the line, few enough to drop, so the copy still
    places the file, unmeasured, late by the copy's delay."""
    key = landed.key("copy_short_stall")
    copy = _copy(key)
    assert copy["outcome"] == "placed" and copy["n_dropped"] >= 1
    recipe = landed.recipe("copy_short_stall")
    late_s = _segment(key)["start_s"] - _session_s(recipe, 0.0)
    assert late_s == pytest.approx(_delay_s(recipe), abs=LAG_BOUND_S)
    assert _tier(key) == "N"


def test_barcodes_and_copy_that_disagree_make_d(landed):
    """Amendment 79's cross-check: the copy's clock runs 3 ms/s off the
    barcodes', so over the span, after the lag is removed, the two placements
    are more than a frame apart: the session is D, though the lag itself is in
    bounds."""
    from wl_preproc.timebase.analog_copy import LAG_CEILING_S

    key = landed.key("copy_disagree")
    copy = _copy(key)
    assert copy["outcome"] == "placed" and bool(copy["lag_measured"])
    assert 0.0 <= copy["lag_s"] <= LAG_CEILING_S
    assert copy["agreement_us"] > 2000.0
    assert _reasons(key) == []
    assert _tier(key) == "D"


def test_a_negative_lag_makes_d(landed):
    """Amendment 79's bound: a copy cannot lead its frame, so a measured lag
    below 0 makes the session D, though the copy and barcodes agree once it is
    removed. Measured as the generator's delay less the shift (ruling P1)."""
    key = landed.key("copy_negative")
    copy = _copy(key)
    assert copy["outcome"] == "placed" and bool(copy["lag_measured"])
    expected_s = _delay_s(landed.recipe("copy_negative")) - NEGATIVE_SHIFT_S
    assert copy["lag_s"] == pytest.approx(expected_s, abs=LAG_BOUND_S)
    assert copy["lag_s"] < 0.0
    assert copy["agreement_us"] <= 2000.0
    assert _reasons(key) == []
    assert _tier(key) == "D"


def test_two_eye_files_are_not_placed_by_the_copy(landed):
    """Amendment 77: a session with more than one eye file is not placed by
    its copy, so no copy is looked for; 3b-3's rules apply: the first half,
    under the box's barcodes, is placed by them, and the second, with none, is
    `no_barcode` and fails the session."""
    from wl_preproc.schema import core, timebase

    key = landed.key("copy_two_files")
    assert not len(timebase.SystemTimebase.Copy & key)
    fit = _fit(key)
    assert (fit["fit_status"], fit["time_source"]) == ("fitted", "barcode")
    (placed,) = (core.Segment & key & {"system": "ohdpi"}).to_dicts()
    assert placed["segment_id"] == "OpenIris-synthetic.txt" and placed["n_barcodes"] > 0
    rejected = (core.RejectedSegment & key & {"system": "ohdpi"}).to_dicts()
    assert [(row["file_path"], row["reason"]) for row in rejected] == [("OpenIris-synthetic-2.txt", "no_barcode")]
    assert _tier(key) == "D"


def test_a_disagreeing_sessions_lag_is_not_carried(landed):
    """Ruling P2: a lag is carried only from a session whose copy passed its
    own checks. The rig's two earlier sessions measured a lag, one whose copy
    disagreed with the barcodes and one below 0; neither is carried, so the
    next session has none: 0 and unmeasured."""
    key = landed.key("copy_after_bad")
    copy = _copy(key)
    assert copy["outcome"] == "placed"
    assert (copy["lag_s"], bool(copy["lag_measured"]), copy["lag_source_datetime"]) == (0.0, False, None)
    assert _tier(key) == "N"


def test_each_eye_file_is_matched_to_its_copy_once(landed):
    """Fix round 1, finding 1: `Segment` places the file from what
    `SystemTimebase.Copy` stored of the placement, so the copy, a 12 h
    file's channel search and whole read, is matched once, never again; and
    the segment carries the stored offset, residual and first sample."""
    from wl_preproc.timebase.analog_copy import FAMILIES

    for name in ("copy_only", "copy_partial", "copy_carried", "copy_short_stall", "copy_stall"):
        (eye_path,) = (landed.session_dir(name) / "ohdpi").glob("*.txt")
        assert landed.placements[eye_path] == 1, name
    for name in ("copy_only", "copy_partial", "copy_carried"):
        copy, segment = _copy(landed.key(name)), _segment(landed.key(name))
        assert copy["signal"] in FAMILIES
        assert (segment["offset_s"], segment["residual_us"], segment["first_sample"]) == (
            copy["offset_s"], copy["residual_us"], copy["first_sample"]), name
        assert segment["start_s"] == segment["offset_s"]


def _scan(values, duration_s=252.0):
    """A stand-in for `segments.scan_system`'s scan of an eye file: its
    barcodes' values, and what the segment row reads."""
    return SimpleNamespace(barcodes=[SimpleNamespace(value=value) for value in values], duration_s=duration_s,
                           stream=SimpleNamespace(n_samples=126_000), n_frame_gaps=0, n_frames_missing=0,
                           n_barcodes_dropped=0)


_KEY = {"subject": "zz-stub", "session_datetime": "2024-01-01 09:00:00", "system": "ohdpi"}
#: The NI's barcodes with a hole in its decode, values 3 to 6 (as
#: `test_partial_sync_box.py`'s `holey_ni`).
_HOLEY_REFERENCE = {value: 0.7 + value for value in (0, 1, 2, 7, 8)}
#: The file the copy placed, as `SystemTimebase.Copy` records it.
_PLACED_FILE = ("ohdpi/eye.txt", 125_990, 41_000_000)
_PLACED = {"offset_s": 0.1, "residual_us": 12.0, "first_sample": 2499, "placed_file": _PLACED_FILE[0],
           "placed_rows": _PLACED_FILE[1], "placed_bytes": _PLACED_FILE[2]}


def test_a_file_in_a_hole_in_the_nis_barcodes_fails_whatever_the_copy_says():
    """Amendment 77, after amendment 76 (fix round 1, finding 2): an eye file
    whose barcodes the NI holds none of, though they lie between its first and
    last, ran under a hole in the NI's own decode, which the copy cannot vouch
    for: `no_shared_barcode`, though the copy placed it. One whose barcodes
    all lie outside the NI's ran outside the box's span, and the copy places
    it; so does one sharing a barcode. With no rate the copy was refused."""
    from wl_preproc.schema.core import Segment

    rate = _RATE
    hole = Segment._place_by_copy(_KEY, "eye.txt", _scan((3, 4, 5)), _HOLEY_REFERENCE, rate, _PLACED, _PLACED_FILE)
    assert hole == (False, {**_KEY, "file_path": "eye.txt", "reason": "no_shared_barcode"})
    for values in ((9, 10), (), (2, 3)):
        placed, row = Segment._place_by_copy(_KEY, "eye.txt", _scan(values), _HOLEY_REFERENCE, rate, _PLACED,
                                             _PLACED_FILE)
        assert placed, values
        assert (row["start_s"], row["offset_s"], row["residual_us"], row["first_sample"]) == (0.1, 0.1, 12.0, 2499)
        assert row["end_s"] == pytest.approx(252.0 / rate.scale + 0.1, abs=1e-12)
        assert row["n_barcodes"] == 0
    refused = Segment._place_by_copy(_KEY, "eye.txt", _scan((3, 4, 5)), _HOLEY_REFERENCE, None, None, _PLACED_FILE)
    assert refused == (False, {**_KEY, "file_path": "eye.txt", "reason": "no_analog_match"})


def _rate():
    from wl_preproc.timebase.fit import RateFit

    return RateFit(fitted_rate_hz=500.015, drift_ppm=30.0, n_matched=0, residual_us_rms=1.0, residual_us_max=2.0)


_RATE = _rate()


@pytest.mark.parametrize("identity, why", [
    (("ohdpi/eye-2.txt", *_PLACED_FILE[1:]), "a second eye file"),
    ((_PLACED_FILE[0], _PLACED_FILE[1] + 500, _PLACED_FILE[2]), "the placed file, rows added"),
    ((_PLACED_FILE[0], _PLACED_FILE[1], _PLACED_FILE[2] + 1), "the placed file, rewritten"),
])
def test_only_the_file_the_copy_placed_takes_its_placement(caplog, identity, why):
    """Fix round 2: the stored placement is the placed file's alone. Another
    eye file under the analog row, one landed since, or the same path changed
    since, is `no_analog_match`, and the log names both files and how to place
    it again; the placed file itself is still placed. Neither borrows the
    first file's offset and first sample, nor mixes them with its own
    extent."""
    import logging

    from wl_preproc.schema import core

    with caplog.at_level(logging.WARNING, logger=core.__name__):
        rejected = core.Segment._place_by_copy(_KEY, "eye-2.txt", _scan(()), _BOX, _RATE, _PLACED, identity)
    assert rejected == (False, {**_KEY, "file_path": "eye-2.txt", "reason": "no_analog_match"}), why
    (warning,) = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert "is not the file the copy placed" in warning and _PLACED_FILE[0] in warning, warning
    assert "delete the session's timing rows" in warning, warning
    placed, row = core.Segment._place_by_copy(_KEY, "eye.txt", _scan(()), _BOX, _RATE, _PLACED, _PLACED_FILE)
    assert placed and row["offset_s"] == 0.1


def test_the_copy_records_the_file_it_placed(landed):
    """Fix round 2: each placement names its file, as the identity
    `Segment` checks it by: the path within the session, its rows and its
    bytes."""
    from wl_preproc.schema.timebase import copy_identity
    from wl_preproc.timebase import segments

    for name in ("copy_only", "copy_partial", "copy_carried"):
        session_dir = landed.session_dir(name)
        (scan,) = segments.scan_system("ohdpi", session_dir / "ohdpi")
        copy = _copy(landed.key(name))
        assert (copy["placed_file"], copy["placed_rows"], copy["placed_bytes"]) == copy_identity(scan, session_dir)
        assert copy["placed_file"] == "ohdpi/OpenIris-synthetic.txt"
        assert copy["placed_rows"] == scan.stream.n_samples and copy["placed_bytes"] == scan.path.stat().st_size


#: The cross-check's unit cases (fix round 1, finding 4): a box whose
#: barcodes cover NI 0.7 to 100.7 s, one a second, and an eye file that starts
#: at NI 50 s, inside the span, its clock 30 ppm fast, so its own barcodes
#: are those from 50.7 s. Its copy's pairs, one a window, are exact, 7 ms
#: late. Its barcodes' line, fitted to its 50 s of them, has a rate 50 ppm off
#: (2 ms frames over 50 s place a rate no better) and is right at their middle.
_SPAN = (0.7, 100.7)
_BOX = {value: 0.7 + value for value in range(101)}
_EYE_SCALE, _EYE_STARTS_S, _COPY_DELAY_S = 1.0 + 30e-6, 50.0, 0.007


def _eye_pairs(late_at: dict | None = None) -> list[tuple[float, float]]:
    """The copy's pairs `(ni_s, device_s)` at each window's centre, each
    `_COPY_DELAY_S` late, and later still by `late_at[device_s]`."""
    late_at = late_at or {}
    return [(device_s / _EYE_SCALE + _EYE_STARTS_S + _COPY_DELAY_S + late_at.get(device_s, 0.0), device_s)
            for device_s in (5.0 + 10.0 * index for index in range(25))]


def _barcode_line(rate_error_ppm: float) -> tuple[float, float]:
    """`(scale, offset_s)` of the eye's barcodes' placement, right at their
    middle, NI 75.7 s."""
    scale = _EYE_SCALE * (1.0 + rate_error_ppm * 1e-6)
    middle_s = 75.7
    return scale, middle_s - (middle_s - _EYE_STARTS_S) * _EYE_SCALE / scale


def test_agreement_is_held_over_the_eye_files_own_barcodes():
    """The eye file starts inside the box's span, so before its first barcode
    the barcodes' line is extrapolated: at the span's start, 75 s from where
    it is right, it is 3.75 ms off, and held there a sound session would be
    D. Held over the file's own first to last shared barcode it is 1.25 ms
    off at each end, and agrees."""
    from wl_preproc.schema.timebase import _cross_check

    barcodes = [SimpleNamespace(value=value) for value in range(50, 101)]
    lag_s, agreement_us = _cross_check(_eye_pairs(), barcodes, _BOX, _barcode_line(50.0), _SPAN, 500.0)
    assert lag_s == pytest.approx(_COPY_DELAY_S, abs=50e-6)
    assert agreement_us == pytest.approx(1250.0, abs=100.0)
    assert agreement_us <= 2000.0


def test_agreement_counts_a_corrected_pair_off_the_barcodes():
    """The barcodes' line exact, one pair inside the span 3 ms later than the
    rest: the line through all 25 barely moves, but that pair is 2.4 ms off
    the barcodes once the lag (pulled 0.6 ms by it) is removed, and the
    agreement says so."""
    from wl_preproc.schema.timebase import _cross_check

    barcodes = [SimpleNamespace(value=value) for value in range(50, 101)]
    pairs = _eye_pairs(late_at={25.0: 0.003})
    lag_s, agreement_us = _cross_check(pairs, barcodes, _BOX, _barcode_line(0.0), _SPAN, 500.0)
    assert lag_s == pytest.approx(_COPY_DELAY_S + 0.003 / 5, abs=20e-6)
    assert agreement_us == pytest.approx(2400.0, abs=100.0)


def test_with_no_pair_over_the_eye_files_barcodes_nothing_is_measured():
    """No pair's NI time lies between the eye file's first and last shared
    barcode: the lag is not measured here (None), and so not held."""
    from wl_preproc.schema.timebase import _cross_check

    barcodes = [SimpleNamespace(value=value) for value in range(50, 54)]
    assert _cross_check(_eye_pairs(), barcodes, _BOX, _barcode_line(0.0), _SPAN, 500.0) is None
    assert _cross_check(_eye_pairs(), [], _BOX, _barcode_line(0.0), _SPAN, 500.0) is None


def test_the_copy_sessions_report_no_error(landed):
    """Every stage runs on these sessions without an error, in either pass."""
    for name in landed:
        for report in landed.reports:
            assert _errors_of(report, landed.key(name)) == [], name


# -- Eye data past the span (amendment 81) --------------------------------------


def _span(landed, name):
    from wl_preproc.timebase.clock import clock_for

    return clock_for(landed.key(name), landed.session_dir(name)).covered_span()


def test_a_copy_placed_eye_is_calibrated_past_the_span(landed):
    """Amendment 81: the copy places every frame of `copy_partial`'s eye file,
    so its calibration uses all ten windows, every one after the box's span
    (8.7 s). `copy_absent`, the same box with no copy, holds ten windows too,
    five inside the span and five after it, and is calibrated from the five
    inside alone (amendment 58, unchanged)."""
    from wl_preproc.eye.span import eye_span
    from wl_preproc.schema import eye

    from tests.schema.test_partial_sync_box import _windows

    partial, absent = landed.key("copy_partial"), landed.key("copy_absent")
    _start_s, end_s = _span(landed, "copy_partial")
    windows = _windows(landed, "copy_partial")
    assert len(windows) == 10 and all(window[0] > end_s for window in windows)
    assert sum(window[1] > end_s for window in _windows(landed, "copy_absent")) == 5
    assert eye_span(partial, landed.session_dir("copy_partial")) is None
    for row in (eye.EyeCalibration & partial).to_dicts():
        assert row["calibration_source"] == "fitted", row
        assert row["n_points"] == 10, row
    for row in (eye.EyeCalibration & absent).to_dicts():
        assert row["calibration_source"] == "fitted", row
        assert row["n_points"] == 5, row


def _saccade_starts_s(landed, name) -> list[float]:
    """The session time of each stored saccade's first row, both eyes and
    every detector, the rows' times re-derived as `test_partial_sync_box.py`
    does."""
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.schema import detect

    from tests.schema.test_partial_sync_box import _eye_recording

    _segment, _path, times = _eye_recording(landed, name)
    runs = (detect.EyeDetection.Run & landed.key(name) & {"label": Label.SACCADE.value}).to_dicts()
    return [float(times[run["run_start"]]) for run in runs]


def test_a_copy_placed_eyes_saccades_past_the_span_are_stored(landed):
    """Amendment 81: detection reads every row of a copy-placed file, so
    saccades are stored past the box's span (the ramps between the windows
    after it); the validity mask names no span, since none cuts. The absent
    copy's file is cut as before: no saccade past the span, and its mask's
    span rows are the span's."""
    from wl_preproc.schema import detect

    for name, past in (("copy_partial", True), ("copy_absent", False)):
        _start_s, end_s = _span(landed, name)
        starts = _saccade_starts_s(landed, name)
        assert starts, name
        assert (max(starts) > end_s) == past, (name, end_s, sorted(starts)[-3:])
        for row in (detect.EyeValidity & landed.key(name)).to_dicts():
            assert row["status"] == "computed", (name, row["reason"])
            assert (row["span_row_stop"] is None) == past, (name, row)


@pytest.fixture(scope="module")
def nwb_files(landed, prefix, tmp_path_factory):
    """The canonical NWB file of `copy_partial` and `copy_absent`, built by
    the daemon in one pass, as `test_partial_sync_box.py::nwb_files` builds
    them: by name, its `NwbFile` row."""
    from wl_preproc import daemon
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import nwb as nwb_schema

    from tests.schema.test_nwb_build import _montage, _request

    keys = {}
    for name in WINDOWED:
        session_key = landed.key(name)
        runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
                 "works_run_id": f"wr-{row['run_number']}"}
                for row in (core.Run & session_key).to_dicts(order_by="run_number")]
        keys[name] = accept(_request(session_key, f"{session_key['subject']}-canonical", _montage(runs), runs),
                            prefix=prefix)
    report = daemon.run_once(prefix=prefix, nwb_root=tmp_path_factory.mktemp("copy-nwb"))
    files = {}
    for name, key in keys.items():
        rows = (nwb_schema.NwbFile & key).to_dicts()
        assert rows, (name, report["errors"])
        assert rows[0]["status"] == "written", (name, rows[0]["reason"] or rows[0]["inspector_findings"])
        files[name] = rows[0]
    return files


def test_a_copy_placed_eyes_nwb_samples_run_past_the_span(landed, nwb_files):
    """Amendment 81: the file holds every eye sample of a copy-placed
    recording, gaze and pupil alike, to its last frame, far past the box's
    span; the absent copy's holds none past the span."""
    from pynwb import NWBHDF5IO

    from tests.schema.test_partial_sync_box import _eye_recording

    for name, past in (("copy_partial", True), ("copy_absent", False)):
        _start_s, end_s = _span(landed, name)
        _segment, _path, raw = _eye_recording(landed, name)
        with NWBHDF5IO(nwb_files[name]["path"], "r") as handle:
            behavior = handle.read().processing["behavior"]
            series = [*behavior["PupilTracking"].time_series.values(),
                      *behavior["EyeTracking"].spatial_series.values()]
            assert len(series) == 6, (name, [item.name for item in series])
            for item in series:
                times = item.timestamps[:]
                assert len(times) == len(item.data) > 0, (name, item.name)
                if past:
                    assert times.max() == pytest.approx(raw.max(), abs=1e-6), (name, item.name)
                    assert times.max() > end_s + 100.0, (name, item.name)
                else:
                    assert times.max() <= end_s, (name, item.name)


def test_a_barcode_placed_eye_is_still_cut(landed):
    """Amendment 81 lifts the cut for a file the copy placed, and only that:
    one whose copy is absent (`copy_absent`) or failed (`copy_stall_box`) is
    placed by barcodes and asks the clock's own span, as before. `copy_only`,
    with no box, has no span to give either way."""
    from wl_preproc.eye.span import eye_span

    for name in ("copy_absent", "copy_stall_box"):
        key = landed.key(name)
        assert _fit(key)["time_source"] == "barcode", name
        span = eye_span(key, landed.session_dir(name))
        assert span is not None and span == _span(landed, name), name
        assert span[1] < _segment(key)["end_s"] - 100.0, name

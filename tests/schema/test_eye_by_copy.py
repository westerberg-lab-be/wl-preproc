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
LAG_BOUND_S = 0.5e-3
PLACED_BOUND_S = 1e-3


class _Landed(dict):
    report: dict
    reports: list

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

    from tests.schema.test_eye_populate import _land

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
            **({"syncbox_stop_s": STOP_S} if box else {}), **update})
        root = tmp_path_factory.mktemp(name)
        truth = generate_session(root, recipe)
        session_dir = root / recipe.session_id
        if name == "copy_disagree":
            _warp_the_copy(session_dir, DISAGREE_PPM, DISAGREE_PIVOT_S)
        if name == "copy_negative":
            _shift_the_copy_earlier(session_dir, NEGATIVE_SHIFT_S)
        if name == "copy_two_files":
            _split_the_eye_file(session_dir, SPLIT_S)
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
    key = landed.key("copy_stall")
    copy = _copy(key)
    assert copy["outcome"] == "failed" and copy["refusal"]
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


def test_the_copy_sessions_report_no_error(landed):
    """Every stage runs on these sessions without an error, in either pass."""
    for name in landed:
        for report in landed.reports:
            assert _errors_of(report, landed.key(name)) == [], name

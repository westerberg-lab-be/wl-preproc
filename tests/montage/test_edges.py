from datetime import datetime

import pytest

from wl_preproc.montage.edges import Placement, RunSpan, gap_name, place, widen_for, window

T0 = datetime(2025, 6, 10, 9, 0)
RUNS = (RunSpan(1, 0.0, 300.0), RunSpan(2, 400.0, 430.0), RunSpan(3, 500.0, 800.0))


def test_a_whole_minute_stands_for_sixty_seconds():
    assert window(datetime(2025, 6, 10, 9, 6), T0, 0.0) == (360.0, 420.0)


def test_a_time_with_seconds_stands_for_that_second():
    assert window(datetime(2025, 6, 10, 9, 5, 30, 250000), T0, 0.0) == (330.0, 331.0)


def test_seconds_are_truncated_on_the_wall_clock_not_the_session_clock():
    t0 = datetime(2025, 6, 10, 9, 0, 0, 250000)
    lo, hi = window(datetime(2025, 6, 10, 9, 5, 30, 900000), t0, 0.0)
    assert lo == pytest.approx(329.75)
    assert hi == pytest.approx(330.75)


def test_the_manifests_t0_widens_by_a_minute_each_side():
    assert window(datetime(2025, 6, 10, 9, 6), T0, 60.0) == (300.0, 480.0)
    assert widen_for("manifest") == 60.0
    for source in ("barcode", "rig_clock", "nidq_meta", None):
        assert widen_for(source) == 0.0


def test_placed_in_the_one_gap_it_touches():
    assert place(330.0, 331.0, RUNS) == Placement("gap", gap=1)
    assert place(-600.0, -540.0, RUNS).gap == 0
    assert place(900.0, 960.0, RUNS).gap == 3


def test_a_window_straddling_a_runs_edge_is_placed_in_its_one_gap():
    assert place(290.0, 310.0, RUNS) == Placement("gap", gap=1)
    assert place(420.0, 440.0, RUNS) == Placement("gap", gap=2)
    assert place(360.0, 420.0, RUNS) == Placement("gap", gap=1)
    assert place(290.0, 410.0, RUNS) == Placement("gap", gap=1)


def test_a_window_wholly_inside_a_run_is_inside_it():
    want = Placement("inside", gap=2, run_number=2)
    assert place(410.0, 411.0, RUNS) == want
    assert place(400.0, 430.0, RUNS) == want


def test_touching_two_gaps_unnamed_is_ambiguous():
    assert place(360.0, 540.0, RUNS) == Placement("ambiguous", candidates=(1, 2))


def test_a_name_chooses_among_the_gaps_touched():
    assert place(360.0, 540.0, RUNS, named_after_run=1) == Placement("gap", gap=1)
    assert place(360.0, 540.0, RUNS, named_after_run=2) == Placement("gap", gap=2)
    assert place(-30.0, 330.0, RUNS, named_after_run=0) == Placement("gap", gap=0)
    assert place(330.0, 331.0, RUNS, named_after_run=1) == Placement("gap", gap=1)


def test_a_name_the_window_does_not_touch_is_never_trusted():
    assert place(360.0, 540.0, RUNS, named_after_run=3) == Placement("unfit", gap=3, candidates=(1, 2))
    assert place(360.0, 540.0, RUNS, named_after_run=0) == Placement("unfit", gap=0, candidates=(1, 2))
    assert place(330.0, 331.0, RUNS, named_after_run=2) == Placement("unfit", gap=2, candidates=(1,))
    assert place(410.0, 411.0, RUNS, named_after_run=2) == Placement("unfit", gap=2, candidates=())
    assert place(360.0, 540.0, RUNS, named_after_run=7) == Placement("unknown_run", run_number=7)


def test_back_to_back_runs():
    runs = (RunSpan(1, 0.0, 300.0), RunSpan(2, 300.0, 600.0))
    assert place(300.0, 301.0, runs) == Placement("inside", gap=2, run_number=2)
    assert place(299.0, 301.0, runs) == Placement("gap", gap=1)


def test_a_whole_minute_does_not_touch_a_run_starting_at_its_end():
    runs = (RunSpan(1, 0.0, 300.0), RunSpan(2, 420.0, 600.0))
    lo, hi = window(datetime(2025, 6, 10, 9, 6), T0, 0.0)
    assert (lo, hi) == (360.0, 420.0)
    assert place(lo, hi, runs) == Placement("gap", gap=1)


def test_with_overlapping_runs_the_first_that_holds_the_window_wins():
    runs = (RunSpan(1, 0.0, 100.0), RunSpan(2, 50.0, 150.0))
    assert place(90.0, 120.0, runs) == Placement("inside", gap=2, run_number=2)
    assert place(40.0, 120.0, runs) == Placement("gap", gap=1)


def test_a_non_finite_value_is_refused_by_name():
    with pytest.raises(ValueError, match="lo"):
        place(float("nan"), 331.0, RUNS)
    with pytest.raises(ValueError, match="hi"):
        place(330.0, float("inf"), RUNS)
    bad = (RunSpan(1, 0.0, float("inf")), RunSpan(2, 400.0, 430.0))
    with pytest.raises(ValueError, match="end_s"):
        place(330.0, 331.0, bad)
    bad = (RunSpan(1, float("nan"), 300.0),)
    with pytest.raises(ValueError, match="start_s"):
        place(330.0, 331.0, bad)


def test_gaps_are_named_by_their_runs():
    assert gap_name(0, RUNS) == "before run 1"
    assert gap_name(1, RUNS) == "between runs 1 and 2"
    assert gap_name(3, RUNS) == "after run 3"

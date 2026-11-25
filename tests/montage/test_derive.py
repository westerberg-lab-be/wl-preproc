from dataclasses import replace
from datetime import datetime

import pytest

from tests.montage.test_edges import RUNS, T0
from wl_preproc.montage.derive import (
    DerivationInput,
    DerivedMontage,
    DerivedProbe,
    PlacedPosition,
    PositionIn,
    RunSelection,
    derive,
)
from wl_preproc.montage.edges import RunSpan


def M(h, m):
    return datetime(2025, 6, 10, h, m)


def S(h, m, s):
    return datetime(2025, 6, 10, h, m, s)


def pos(n, inserted_at, ended_at=None, end_kind=None, serial="S1", inserted_after_run=None, ended_after_run=None):
    return PositionIn(serial, n, inserted_at, ended_at, end_kind, inserted_after_run, ended_after_run)


def facts(positions, selections=None, **kw):
    if selections is None:
        selections = {(r.run_number, "S1"): RunSelection("A", "A", False) for r in RUNS}
    base = DerivationInput(
        has_ephys=True,
        runs=RUNS,
        positions=positions,
        positions_seq=4,
        t0=T0,
        t0_source="barcode",
        census_complete=True,
        selections=selections,
        recorded_serials=frozenset({"S1"}),
    )
    return replace(base, **kw)


def runs_of(d):
    return [m.run_numbers for m in d.montages]


def probes_of(d):
    return [m.probes for m in d.montages]


MOVE = (pos(1, M(8, 50), S(9, 5, 30), "moved"), pos(2, S(9, 5, 30)))


def test_a_move_between_runs_splits_the_montages():
    d = derive(facts(MOVE))
    assert d.status == "placed"
    assert d.positions_seq == 4
    assert d.montages == (
        DerivedMontage(1, 0.0, 400.0, (1,), (DerivedProbe("S1", 1, "A", (1,)),)),
        DerivedMontage(2, 400.0, 800.0, (2, 3), (DerivedProbe("S1", 2, "A", (2, 3)),)),
    )
    assert d.placed == (PlacedPosition("S1", 1, (1,)), PlacedPosition("S1", 2, (2, 3)))
    assert d.flags == ()


def test_a_first_insertion_and_a_last_withdrawal_split_nothing():
    d = derive(facts((pos(1, S(9, 5, 30), M(9, 15), "withdrawn"),)))
    assert d.status == "placed"
    assert d.montages == (DerivedMontage(1, 0.0, 800.0, (1, 2, 3), (DerivedProbe("S1", 1, "A", (2, 3)),)),)
    assert d.flags == ()


def test_a_reinsertion_after_a_withdrawal_splits():
    d = derive(facts((pos(1, M(8, 50), S(9, 5, 30), "withdrawn"), pos(2, S(9, 7, 30)))))
    assert d.status == "placed"
    assert runs_of(d) == [(1, 2), (3,)]
    assert probes_of(d) == [(DerivedProbe("S1", 1, "A", (1,)),), (DerivedProbe("S1", 2, "A", (3,)),)]
    assert d.flags == ()


def test_a_selection_change_between_runs_splits():
    selections = {(1, "S1"): RunSelection("A", "A", False), (2, "S1"): RunSelection("A", "A", False),
                  (3, "S1"): RunSelection("B", "B", False)}
    d = derive(facts((pos(1, M(8, 50)),), selections))
    assert d.status == "placed"
    assert runs_of(d) == [(1, 2), (3,)]
    assert probes_of(d) == [(DerivedProbe("S1", 1, "A", (1, 2)),), (DerivedProbe("S1", 1, "B", (3,)),)]
    assert d.flags == ()


def test_a_selection_change_inside_a_run_splits_after_it():
    selections = {(1, "S1"): RunSelection("A", "A", False), (2, "S1"): RunSelection("A", "B", True),
                  (3, "S1"): RunSelection("B", "B", False)}
    d = derive(facts((pos(1, M(8, 50)),), selections))
    assert d.status == "placed"
    assert runs_of(d) == [(1, 2), (3,)]
    assert probes_of(d) == [(DerivedProbe("S1", 1, "A", (1,)),), (DerivedProbe("S1", 1, "B", (3,)),)]
    assert d.flags == ()


def test_a_selection_that_returns_inside_a_run_still_leaves_the_run_out():
    # A -> B -> A inside run 2: its first and last hashes agree, but its selection changed inside it (D6).
    selections = {(1, "S1"): RunSelection("A", "A", False), (2, "S1"): RunSelection("A", "A", True),
                  (3, "S1"): RunSelection("A", "A", False)}
    d = derive(facts((pos(1, M(8, 50)),), selections))
    assert d.status == "placed"
    assert runs_of(d) == [(1, 2), (3,)]
    assert probes_of(d) == [(DerivedProbe("S1", 1, "A", (1,)),), (DerivedProbe("S1", 1, "A", (3,)),)]


def test_an_edge_inside_a_run_is_placed_after_it():
    d = derive(facts((pos(1, M(8, 50), S(9, 6, 50), "moved"), pos(2, S(9, 6, 50)))))
    assert d.status == "placed"
    assert runs_of(d) == [(1, 2), (3,)]
    assert probes_of(d) == [(DerivedProbe("S1", 1, "A", (1,)),), (DerivedProbe("S1", 2, "A", (3,)),)]
    assert [(f["code"], f["run_number"]) for f in d.flags] == [("position_edge_inside_run", 2)] * 2
    assert {"block_number": None, "segment_id": None}.items() <= d.flags[0].items()
    assert ("probe S1's end (insertion 1) falls inside run 2: it is placed after run 2, and run 2 is sorted for probe "
            "S1 on neither side") in [f["message"] for f in d.flags]
    assert ("probe S1's insertion (insertion 2) falls inside run 2: it is placed after run 2, and run 2 is sorted for "
            "probe S1 on neither side") in [f["message"] for f in d.flags]


AMBIGUOUS = (pos(1, M(8, 50), M(9, 7), "moved"), pos(2, M(9, 7)))


def test_an_ambiguous_edge_lists_no_montage():
    d = derive(facts(AMBIGUOUS, t0_source="manifest"))
    assert d.status == "ambiguous"
    assert d.positions_seq == 4
    assert d.montages == ()
    assert d.placed == ()
    assert len(d.flags) == 2
    assert {f["code"] for f in d.flags} == {"position_edge_ambiguous"}
    messages = [f["message"] for f in d.flags]
    assert ("probe S1's end (insertion 1) at 2025-06-10 09:07:00 could lie between runs 1 and 2 or between runs 2 and "
            "3: name the gap with ended_after_run; no montage is listed until then") in messages
    assert ("probe S1's insertion (insertion 2) at 2025-06-10 09:07:00 could lie between runs 1 and 2 or between "
            "runs 2 and 3: name the gap with inserted_after_run; no montage is listed until then") in messages


def test_an_empty_set_is_an_answer():
    d = derive(facts(()))
    assert d.status == "placed"
    assert d.montages == (DerivedMontage(1, 0.0, 800.0, (1, 2, 3), ()),)
    assert d.placed == ()
    assert [f["code"] for f in d.flags] == ["probe_without_position"]


def test_a_named_gap_places_the_ambiguous_edge():
    named = (replace(AMBIGUOUS[0], ended_after_run=1), replace(AMBIGUOUS[1], inserted_after_run=1))
    d = derive(facts(named, t0_source="manifest"))
    assert d.status == "placed"
    assert runs_of(d) == [(1,), (2, 3)]
    assert probes_of(d) == [(DerivedProbe("S1", 1, "A", (1,)),), (DerivedProbe("S1", 2, "A", (2, 3)),)]
    assert d.flags == ()


def test_a_name_its_time_does_not_touch_is_ambiguous():
    named = (replace(AMBIGUOUS[0], ended_after_run=3), replace(AMBIGUOUS[1], inserted_after_run=3))
    d = derive(facts(named, t0_source="manifest"))
    assert d.status == "ambiguous"
    assert d.montages == ()
    assert d.placed == ()
    assert {f["code"] for f in d.flags} == {"position_edge_ambiguous"}
    assert ("probe S1's end (insertion 1) at 2025-06-10 09:07:00 names the gap after run 3 in ended_after_run, which "
            "its time does not touch; no montage is listed until the name or the time is corrected") in [
        f["message"] for f in d.flags]


def test_a_name_that_is_not_a_measured_run_is_ambiguous():
    named = (replace(AMBIGUOUS[0], ended_after_run=7), replace(AMBIGUOUS[1], inserted_after_run=1))
    d = derive(facts(named, t0_source="manifest"))
    assert d.status == "ambiguous"
    assert d.montages == ()
    assert [f["message"] for f in d.flags] == [
        "probe S1's end (insertion 1) names run 7 in ended_after_run, which is not a measured run of this session; no "
        "montage is listed until it names one"]


def test_named_gaps_that_cross_are_ambiguous():
    named = (replace(AMBIGUOUS[0], ended_after_run=2), replace(AMBIGUOUS[1], inserted_after_run=1))
    d = derive(facts(named, t0_source="manifest"))
    assert d.status == "ambiguous"
    assert d.montages == ()
    assert d.placed == ()
    assert [(f["code"], f["message"]) for f in d.flags] == [(
        "position_edge_ambiguous",
        "probe S1's insertions 1 and 2 overlap once placed between runs; no montage is listed until their gaps are "
        "named")]


def test_a_position_that_ends_before_it_starts_is_ambiguous():
    one = (pos(1, M(9, 7), M(9, 7), "withdrawn", inserted_after_run=2, ended_after_run=1),)
    d = derive(facts(one, t0_source="manifest"))
    assert d.status == "ambiguous"
    assert d.montages == ()
    assert [f["message"] for f in d.flags] == [
        "probe S1's insertion 1 ends between runs 1 and 2, before it starts between runs 2 and 3; no montage is listed "
        "until its gaps are named"]


@pytest.mark.parametrize("missing", [
    {"positions": None}, {"t0": None}, {"census_complete": False}, {"runs": ()},
])
def test_it_waits_for_what_it_needs(missing):
    d = derive(facts(**{"positions": MOVE, **missing}))
    assert d.status == "waiting"
    assert d.montages == ()
    assert d.placed == ()
    assert d.positions_seq == 4


@pytest.mark.parametrize("positions", [None, MOVE])
def test_a_session_without_ephys_has_one_montage(positions):
    d = derive(facts(positions, has_ephys=False))
    assert d.status == "not_needed"
    assert d.montages == (DerivedMontage(1, 0.0, 800.0, (1, 2, 3), ()),)
    assert d.placed == ()
    assert d.flags == ()


def test_a_zero_length_last_run_is_nudged():
    runs = (RUNS[0], RUNS[1], RunSpan(3, 500.0, 500.0))
    d = derive(facts(MOVE, runs=runs))
    assert d.montages[-1].end_s == 500.001
    assert d.montages[0].end_s == 400.0


def test_a_recorded_serial_without_a_position_is_flagged():
    d = derive(facts(MOVE, recorded_serials=frozenset({"S1", "S2"})))
    assert d.status == "placed"
    assert [(f["code"], f["message"], f["run_number"]) for f in d.flags] == [(
        "probe_without_position",
        "probe S2 is recorded, and no position from wl.works names it: its runs are sortable in no montage, and its "
        "area is unknown",
        None)]
    assert all(p.serial != "S2" for m in d.montages for p in m.probes)


def test_a_serial_the_census_never_names_bounds_montages_alone():
    intan = tuple(replace(p, serial="INTAN-1") for p in MOVE)
    d = derive(facts(intan, selections={}))
    assert d.status == "placed"
    assert runs_of(d) == [(1,), (2, 3)]
    assert probes_of(d) == [(DerivedProbe("INTAN-1", 1, None, (1,)),), (DerivedProbe("INTAN-1", 2, None, (2, 3)),)]


def test_a_recorded_serial_is_not_sortable_where_it_has_no_selection():
    selections = {(1, "S1"): RunSelection("A", "A", False), (3, "S1"): RunSelection("A", "A", False)}
    d = derive(facts((pos(1, M(8, 50)),), selections))
    assert d.montages == (DerivedMontage(1, 0.0, 800.0, (1, 2, 3), (DerivedProbe("S1", 1, "A", (1, 3)),)),)
    assert d.placed == (PlacedPosition("S1", 1, (1, 2, 3)),)


def test_a_probe_with_no_sortable_run_in_a_montage_is_listed_without_a_hash():
    selections = {(1, "S1"): RunSelection("A", "A", False), (2, "S1"): RunSelection("A", "B", True),
                  (3, "S1"): RunSelection("B", "B", False)}
    d = derive(facts((pos(1, M(8, 50)),), selections, runs=RUNS[1:]))
    assert runs_of(d) == [(2,), (3,)]
    assert probes_of(d) == [(DerivedProbe("S1", 1, None, ()),), (DerivedProbe("S1", 1, "B", (3,)),)]

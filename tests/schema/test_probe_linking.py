"""Linking each placed position to the probe the recording names (design
specs `2026-09-30-nwb-probes-design.md` section 2.3 and
`2026-11-24-montages-design.md` section 4.7): each daemon pass writes
`ProbeInsertion`, `InsertionLocation` and `SegmentConfig` from
`core.PlacedPosition`, `InsertionReport` and `ProbeCensus`, in either arrival
order. A file joins every insertion whose position overlaps its time, and a
corrected report or position relinks on the next pass. Every link here comes
from a `PUT /positions` (`tests/positions.py::put`); a canonical request
supplies the report where a test needs one."""

from __future__ import annotations

import datetime

import pytest

from tests.identities import new_animal, new_serial
from tests.positions import before_t0, land, put
from tests.schema.test_spikeglx_restart import RESTART

_AIM = {"area": "V4d", "atlas": "CHARM", "atlas_level": 6}


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def _landed(tmp_path_factory, serial_2=None, **update):
    """A new animal's CI session with runs, whose `imec0` is a new serial and which
    records `serial_2` on a second probe when it is named. Returns its key and
    the serials; the event stage has read its runs, as it has before the
    census in a daemon pass (a request names measured runs, design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3.2)."""
    from wl_preproc import daemon

    session = new_animal().session()
    serial = new_serial()
    if serial_2 is not None:
        update["extra_probes"] = [{"serial": serial_2, "part_number": "NP1032", "bank": 1}]
    key = land(tmp_path_factory, systems=("syncbox", "spikeglx"), subject=session.subject,
               session_id=session.session_id, session_datetime=session.session_datetime, probe_serial=serial,
               **update)
    daemon._populate_event_stage()
    return key, serial


def _position(serial, number, inserted_at, **more):
    return {"serial": serial, "insertion_number": number, "inserted_at": inserted_at, **more}


def _report(key, idempotency_key, probes, prefix):
    """wl.works' job request for the session's canonical, carrying `probes`,
    each sorting every run. A second one for the same montage returns the
    same activation, and its report is recorded all the same. Montage 0 is
    stored first with the probes' positions, as this host would list it (`tests/positions.py::seed_montage`)."""
    from tests.positions import seed_montage
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
             "works_run_id": f"wr-{row['run_number']}"} for row in (core.Run & key).to_dicts(order_by="run_number")]
    seed_montage(key, {"montage_id": 0, "start_s": 0.0, "end_s": 16.0}, [
        {"serial": probe["serial"], "insertion_number": probe["insertion_number"],
         "sortable_runs": [run["run_number"] for run in runs]} for probe in probes])
    return accept(JobRequest(
        domain="neural", parameters={}, idempotency_key=idempotency_key,
        selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC), "montage_id": 0,
                   "probe_runs": {probe["serial"]: [run["run_number"] for run in runs] for probe in probes}},
        metadata=MetadataBundle(runs=runs, probes=probes, experimenter="jw", subject=key["subject"], task_types=[]),
    ), prefix=prefix)


def _links(key):
    """`{insertion_number: (serial, trajectory, aim, {segment: config})}`."""
    from wl_preproc.schema import ephys

    links = {}
    for row in (ephys.ProbeInsertion & key).to_dicts():
        where = {**key, "insertion_number": row["insertion_number"]}
        location = (ephys.InsertionLocation & where).to_dicts()
        aim = (location[0]["area"], location[0]["atlas"], location[0]["atlas_level"]) if location else None
        configs = {c["segment_id"]: c["electrode_config_hash"] for c in (ephys.SegmentConfig & where).to_dicts()}
        links[row["insertion_number"]] = (row["probe_serial"], row["trajectory_id"], aim, configs)
    return links


def _recorded(key, serial):
    """`{segment: config}` for what the census recorded of `serial`."""
    from wl_preproc.schema import ephys

    parts = ephys.ProbeCensus.Probe & key & {"probe_serial": serial}
    return {p["segment_id"]: p["electrode_config_hash"] for p in parts.to_dicts()}


def test_a_moved_probe_links_its_file_to_both_insertions(daemon_module, prefix, tmp_path_factory):
    """One serial moved after run 1 with no restart, so one file spans the
    move: it joins both insertions."""
    from wl_preproc.schema import core

    key, serial = _landed(tmp_path_factory)
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial, 1, before_t0(key), ended_at=before_t0(key, 0), end_kind="moved", ended_after_run=1),
              _position(serial, 2, before_t0(key, 0), inserted_after_run=1)], prefix=prefix)
    report = daemon_module.run_once(prefix=prefix)

    assert not [error for error in report["errors"] if key["subject"] in error]
    assert len(core.PlacedPosition & key & "start_s IS NOT NULL") == 2
    assert _links(key) == {1: (serial, None, None, _recorded(key, serial)),
                           2: (serial, None, None, _recorded(key, serial))}
    assert len(_recorded(key, serial)) == 1


@pytest.mark.parametrize("positions_first", [True, False], ids=["positions-first", "census-first"])
def test_a_report_links_to_the_probe_the_recording_names(daemon_module, prefix, tmp_path_factory, positions_first):
    serial_2 = new_serial()
    key, serial_1 = _landed(tmp_path_factory, serial_2, spikeglx_restart={**RESTART, "probe_bank": 1})
    if positions_first:
        # No ClockReference yet: t = 0 is the session's own time, ten minutes on.
        early = key["session_datetime"] - datetime.timedelta(minutes=10)
        put(key, [_position(serial_1, 1, early), _position(serial_2, 2, early)], prefix=prefix)
    daemon_module.run_once(prefix=prefix)
    if not positions_first:
        positions = [_position(serial_1, 1, before_t0(key)), _position(serial_2, 2, before_t0(key))]
        put(key, positions, prefix=prefix)
        daemon_module.run_once(prefix=prefix)
    _report(key, f"{key['subject']}-k1", [
        {"serial": serial_1, "insertion_number": 1, "trajectory_id": "T-1", "target": _AIM},
        {"serial": serial_2, "insertion_number": 2}], prefix)
    daemon_module.run_once(prefix=prefix)

    assert _links(key) == {
        1: (serial_1, "T-1", ("V4d", "CHARM", 6), _recorded(key, serial_1)),
        2: (serial_2, None, None, _recorded(key, serial_2)),
    }
    # One config per segment: imec0 changed bank at the restart.
    assert len(set(_recorded(key, serial_1).values())) == 2


def test_a_position_links_before_any_report(daemon_module, prefix, tmp_path_factory):
    key, serial = _landed(tmp_path_factory)
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial, 1, before_t0(key))], prefix=prefix)
    daemon_module.run_once(prefix=prefix)

    assert _links(key) == {1: (serial, None, None, _recorded(key, serial))}


def test_a_changed_position_relinks_on_the_next_pass(daemon_module, prefix, tmp_path_factory):
    """Positions at seq 1 name serial A, and at seq 2 serial B, before any
    request: the insertion's probe, and its segments, follow."""
    serial_b = new_serial()
    key, serial_a = _landed(tmp_path_factory, serial_b)
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial_a, 1, before_t0(key))], seq=1, prefix=prefix)
    daemon_module.run_once(prefix=prefix)
    assert _links(key) == {1: (serial_a, None, None, _recorded(key, serial_a))}

    put(key, [_position(serial_b, 1, before_t0(key))], seq=2, prefix=prefix)
    daemon_module.run_once(prefix=prefix)

    assert _links(key) == {1: (serial_b, None, None, _recorded(key, serial_b))}


def test_a_corrected_report_relinks_on_the_next_pass(daemon_module, prefix, tmp_path_factory):
    """A later canonical changed the first insertion's trajectory and aim,
    and the second loses its aim: what changed is updated in place."""
    serial_2 = new_serial()
    key, serial_1 = _landed(tmp_path_factory, serial_2)
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial_1, 1, before_t0(key)), _position(serial_2, 2, before_t0(key))], prefix=prefix)
    daemon_module.run_once(prefix=prefix)
    subject = key["subject"]
    _report(key, f"{subject}-k1", [{"serial": serial_1, "insertion_number": 1, "target": _AIM},
                                   {"serial": serial_2, "insertion_number": 2, "target": _AIM}], prefix)
    daemon_module.run_once(prefix=prefix)
    _report(key, f"{subject}-k2", [
        {"serial": serial_1, "insertion_number": 1, "trajectory_id": "T-9",
         "target": {"area": "V4v", "atlas": "CHARM", "atlas_level": 6}},
        {"serial": serial_2, "insertion_number": 2}], prefix)
    daemon_module.run_once(prefix=prefix)

    assert _links(key) == {
        1: (serial_1, "T-9", ("V4v", "CHARM", 6), _recorded(key, serial_1)),
        2: (serial_2, None, None, _recorded(key, serial_2)),
    }


def test_a_serial_the_recording_never_named_links_nothing(daemon_module, prefix, tmp_path_factory):
    """A typo, or a swapped probe: `ProbeInsertion` needs a recorded
    `Probe`, and there is none to point at."""
    key, serial = _landed(tmp_path_factory)
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial, 1, before_t0(key)), _position(new_serial(), 2, before_t0(key))], prefix=prefix)
    report = daemon_module.run_once(prefix=prefix)

    assert set(_links(key)) == {1}
    # Nothing linked because nothing should be, not because linking failed.
    assert not [error for error in report["errors"] if key["subject"] in error]


def test_a_probe_without_geometry_is_not_linked(daemon_module, prefix, tmp_path_factory):
    """An unknown part number has no `Probe` row and no config (Phase 2a's
    invariant), so its position stays unlinked, and the file says why."""
    from tests.schema.test_probe_census import _unknown_type
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon

    serial = new_serial()
    session = new_animal().session()
    _recipe, key = _session(tmp_path_factory, _unknown_type, subject=session.subject,
                            session_id=session.session_id, probe_serial=serial, runs=True,
                            session_datetime=session.session_datetime)
    daemon._populate_event_stage()
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial, 1, before_t0(key))], prefix=prefix)
    daemon_module.run_once(prefix=prefix)

    assert _links(key) == {}


def test_an_insertion_no_report_and_no_position_made_is_left_alone(daemon_module, prefix, tmp_path_factory):
    """The linker takes back only links it could have made, and only for an
    insertion a report or a position numbers; one with neither was made some
    other way -- by hand, or a synthetic one a test's sort points at -- and is
    not its to remove."""
    from wl_preproc.schema import ephys

    key, serial = _landed(tmp_path_factory)
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial, 1, before_t0(key))], prefix=prefix)
    daemon_module.run_once(prefix=prefix)
    ephys.ProbeInsertion.insert1({**key, "insertion_number": 5, "probe_serial": serial})

    report = daemon_module.run_once(prefix=prefix)

    assert _links(key) == {1: (serial, None, None, _recorded(key, serial)),
                           5: (serial, None, None, {})}
    assert not [error for error in report["errors"] if key["subject"] in error]


def test_a_move_across_a_restart_links_each_insertion_only_the_files_its_position_overlaps(
        daemon_module, prefix, tmp_path_factory):
    """The restart is inside run 2, which starts 0.001 s after run 1 ends (9 s)
    and spans both files: position 1 (run 1) overlaps the first file only, so
    the second, starting at 11.5 s, stays unlinked from it; position 2 (run 2)
    overlaps both."""
    key, serial = _landed(tmp_path_factory, spikeglx_restart={**RESTART, "probe_bank": 1})
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial, 1, before_t0(key), ended_at=before_t0(key, 0), end_kind="moved", ended_after_run=1),
              _position(serial, 2, before_t0(key, 0), inserted_after_run=1)], prefix=prefix)
    daemon_module.run_once(prefix=prefix)

    first, second = sorted(_recorded(key, serial))  # file names sort in time order
    links = _links(key)
    assert set(links[1][3]) == {first}
    assert set(links[2][3]) == {first, second}


def test_a_position_moved_to_an_unrecorded_serial_takes_its_link_back(daemon_module, prefix, tmp_path_factory):
    from wl_preproc.schema import core

    key, serial = _landed(tmp_path_factory)
    daemon_module.run_once(prefix=prefix)
    put(key, [_position(serial, 1, before_t0(key))], seq=1, prefix=prefix)
    daemon_module.run_once(prefix=prefix)
    assert set(_links(key)) == {1}

    stranger = new_serial()
    put(key, [_position(stranger, 1, before_t0(key))], seq=2, prefix=prefix)
    report = daemon_module.run_once(prefix=prefix)

    assert (core.PlacedPosition & key).fetch1("probe_serial") == stranger
    assert _links(key) == {}
    assert not [error for error in report["errors"] if key["subject"] in error]

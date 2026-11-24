"""The montage stage, stored (design spec `2026-11-24-montages-design.md`
sections 4.1-4.4; plan decisions D6-D10, D13): each daemon pass derives every
listable session's montages and shared groups from its stored positions, its
runs and its census, and rewrites them only when the result changes, until the
session's first accepted request.

The synthetic sessions' t = 0 comes from the manifest, so an edge's window
widens by a minute each side and any time near the session touches every gap:
an edge near the runs names its gap (`*_after_run`), as wl.works would. An
insertion ten minutes before t = 0 (`before_t0`) touches only the gap before
the first run."""

from __future__ import annotations

import datetime

import pytest

from tests.identities import new_animal, new_serial
from tests.positions import before_t0, land, put
from tests.schema.test_spikeglx_restart import RESTART

_EPHYS = ("syncbox", "spikeglx")
_MONTAGE_TABLES = ("MontageDerivation", "MontageDerivation.Flag", "Montage", "MontageProbe", "MontageSortableRun",
                   "PlacedPosition", "SharedGroup", "SharedGroup.Member", "SharedGroup.SortableRun")


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def _landed(tmp_path_factory, systems=_EPHYS, **update) -> tuple[dict, str]:
    """A new animal's CI session with runs, recording `systems`; returns its key and `imec0`'s serial."""
    session = new_animal().session()
    serial = new_serial()
    key = land(tmp_path_factory, systems=systems, subject=session.subject, session_id=session.session_id,
               session_datetime=session.session_datetime, probe_serial=serial, **update)
    return key, serial


def _pass(daemon, prefix, key) -> dict:
    report = daemon.run_once(prefix=prefix)
    mine = [error for error in report["errors"] if key["subject"] in error]
    assert not mine, mine
    return report


def _t0(key) -> datetime.datetime:
    """t = 0's minute: under the manifest's widening its window touches every gap of these short sessions."""
    return before_t0(key, 0)


def _position(serial: str, number: int, inserted_at: datetime.datetime, **more) -> dict:
    return {"serial": serial, "insertion_number": number, "inserted_at": inserted_at, **more}


def _table(name: str):
    from wl_preproc.schema import core

    table = core
    for part in name.split("."):
        table = getattr(table, part)
    return table


def _rows(key) -> dict:
    """Every montage table's rows of the session, `derived_at` included."""
    return {name: sorted((_table(name) & key).to_dicts(), key=repr) for name in _MONTAGE_TABLES}


def _derivation(key) -> tuple[str, int | None]:
    from wl_preproc.schema import core

    row = (core.MontageDerivation & key).fetch1()
    return row["positions_status"], row["positions_seq"]


def _runs(key) -> list[dict]:
    from wl_preproc.schema import core

    return (core.Run & key).to_dicts(order_by="run_number")


def _montages(key) -> list[tuple[int, float, float]]:
    from wl_preproc.schema import core

    return [(row["montage_id"], row["start_s"], row["end_s"])
            for row in (core.Montage & key).to_dicts(order_by="montage_id")]


def _probes(key) -> dict:
    """`{(montage_id, serial): (insertion_number, electrode_config_hash, [sortable runs])}`."""
    from wl_preproc.schema import core

    probes = {}
    for row in (core.MontageProbe & key).to_dicts():
        where = {**key, "montage_id": row["montage_id"], "probe_serial": row["probe_serial"]}
        runs = sorted(int(n) for n in (core.MontageSortableRun & where).to_arrays("run_number"))
        probes[(row["montage_id"], row["probe_serial"])] = (
            row["insertion_number"], row["electrode_config_hash"], runs)
    return probes


def _census(key, serial) -> list[str]:
    """The serial's `electrode_config_hash` in each SpikeGLX segment, by the segment's start."""
    from wl_preproc.schema import core, ephys

    segments = (core.Segment & key & {"system": "spikeglx"}).proj("start_s")
    rows = (ephys.ProbeCensus.Probe * segments & {"probe_serial": serial}).to_dicts(order_by="start_s")
    return [row["electrode_config_hash"] for row in rows]


def test_a_session_without_ephys_has_one_montage_at_once(daemon_module, prefix, tmp_path_factory):
    from wl_preproc.schema import core

    key, _serial = _landed(tmp_path_factory, systems=("syncbox",))
    _pass(daemon_module, prefix, key)

    assert _derivation(key) == ("not_needed", None)
    first, second = _runs(key)
    assert _montages(key) == [(1, first["run_start_time"], second["run_stop_time"])]
    assert not core.MontageProbe & key


def test_positions_for_a_session_without_ephys_change_nothing(daemon_module, prefix, tmp_path_factory):
    """Review Focus 4: they are stored and listed by their seq, and the session keeps its one montage."""
    key, serial = _landed(tmp_path_factory, systems=("syncbox",))
    _pass(daemon_module, prefix, key)
    montages = _montages(key)

    put(key, [_position(serial, 1, before_t0(key))], seq=4, prefix=prefix)
    _pass(daemon_module, prefix, key)

    assert _derivation(key) == ("not_needed", 4)
    assert _montages(key) == montages and len(montages) == 1


def test_a_recording_waits_for_its_positions(daemon_module, prefix, tmp_path_factory):
    from wl_preproc.schema import core

    key, serial = _landed(tmp_path_factory)
    _pass(daemon_module, prefix, key)
    assert _derivation(key) == ("waiting", None)
    assert not core.Montage & key

    put(key, [_position(serial, 1, before_t0(key))], seq=1, prefix=prefix)
    _pass(daemon_module, prefix, key)

    assert _derivation(key) == ("placed", 1)
    first, second = _runs(key)
    assert _montages(key) == [(1, first["run_start_time"], second["run_stop_time"])]
    (config,) = set(_census(key, serial))
    assert _probes(key) == {(1, serial): (1, config, [1, 2])}
    placed = (core.PlacedPosition & key).fetch1()
    assert (placed["insertion_number"], placed["probe_serial"], placed["start_s"], placed["end_s"]) == (
        1, serial, first["run_start_time"], second["run_stop_time"])


def test_positions_sent_on_landing_are_placed_on_the_first_pass(daemon_module, prefix, tmp_path_factory):
    """Review Focus 2: no `ClockReference` is stored yet, so the time is ten minutes before the session's
    `session_datetime`, the manifest's t = 0 in these sessions."""
    key, serial = _landed(tmp_path_factory)
    put(key, [_position(serial, 1, key["session_datetime"] - datetime.timedelta(minutes=10))], seq=1,
        prefix=prefix)
    _pass(daemon_module, prefix, key)

    assert _derivation(key) == ("placed", 1)


def test_a_moved_probe_splits_the_montages_and_the_other_probe_shares_its_electrodes(
        daemon_module, prefix, tmp_path_factory):
    from wl_preproc.schema import core, ephys

    other = new_serial()
    key, serial = _landed(tmp_path_factory, extra_probes=[{"serial": other, "part_number": "NP1032", "bank": 1}])
    _pass(daemon_module, prefix, key)
    put(key, [
        _position(serial, 1, before_t0(key)),
        _position(other, 2, before_t0(key), ended_at=_t0(key), end_kind="moved", ended_after_run=1),
        _position(other, 3, _t0(key), inserted_after_run=1),
    ], seq=1, prefix=prefix)
    _pass(daemon_module, prefix, key)

    assert _derivation(key) == ("placed", 1)
    first, second = _runs(key)
    assert _montages(key) == [(1, first["run_start_time"], second["run_start_time"]),
                              (2, second["run_start_time"], second["run_stop_time"])]
    (config,) = set(_census(key, serial))
    (other_config,) = set(_census(key, other))
    assert _probes(key) == {(1, serial): (1, config, [1]), (2, serial): (1, config, [2]),
                            (1, other): (2, other_config, [1]), (2, other): (3, other_config, [2])}

    group = (core.SharedGroup & key).fetch1()
    assert (group["group_id"], group["probe_serial"], group["insertion_number"]) == (1, serial, 1)
    # The identity intersection: one selection over both montages is the same config.
    assert group["electrode_config_hash"] == config
    assert group["n_electrodes"] == 4
    assert group["probe_type"] == (ephys.ElectrodeConfig & {"electrode_config_hash": config}).fetch1("probe_type")
    where = {**key, "group_id": 1}
    assert sorted(int(m) for m in (core.SharedGroup.Member & where).to_arrays("montage_id")) == [1, 2]
    assert sorted(int(r) for r in (core.SharedGroup.SortableRun & where).to_arrays("run_number")) == [1, 2]


def test_a_bank_change_inside_a_run_leaves_the_run_unsortable(daemon_module, prefix, tmp_path_factory):
    """SpikeGLX restarts at 11 s, inside run 2, with another bank (section 4.3)."""
    from wl_preproc.schema import core

    key, serial = _landed(tmp_path_factory, spikeglx_restart={**RESTART, "probe_bank": 1})
    _pass(daemon_module, prefix, key)
    put(key, [_position(serial, 1, before_t0(key))], seq=1, prefix=prefix)
    _pass(daemon_module, prefix, key)

    assert _derivation(key) == ("placed", 1)
    first, second = _runs(key)
    assert _montages(key) == [(1, first["run_start_time"], second["run_stop_time"])]
    before, after = _census(key, serial)
    assert before != after
    assert _probes(key) == {(1, serial): (1, before, [1])}
    assert not core.SharedGroup & key


def test_an_accepted_request_fixes_the_derivation(daemon_module, prefix, tmp_path_factory):
    """D8: once a request is accepted the session is never derived again, so a change to its positions --
    here made in the database, past `PUT /positions`' own refusal -- changes none of its rows."""
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
    from wl_preproc.montage.derive import derive
    from wl_preproc.montage.stage import gather_input
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request

    key, serial = _landed(tmp_path_factory)
    _pass(daemon_module, prefix, key)
    put(key, [_position(serial, 1, before_t0(key), ended_at=_t0(key), end_kind="withdrawn", ended_after_run=1)],
        seq=1, prefix=prefix)
    _pass(daemon_module, prefix, key)
    assert _derivation(key) == ("placed", 1)
    assert [probe[2] for probe in _probes(key).values()] == [[1]]

    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
             "works_run_id": f"wr-{row['run_number']}"} for row in _runs(key)]
    accept(JobRequest(
        domain="neural", parameters={}, idempotency_key=f"{key['subject']}-canonical",
        selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC), "montage_id": 1},
        metadata=MetadataBundle(runs=runs, probes=[], experimenter="jw", subject=key["subject"], task_types=[]),
    ), prefix=prefix)
    assert request.first_accepted_request(key) == f"{key['subject']}-canonical"
    stored = _rows(key)

    request.PositionSet.Position.update1({**key, "insertion_number": 1, "ended_after_run": 2})
    # Derived again, the probe would now cover both runs.
    (montage,) = derive(gather_input(key)).montages
    assert [probe.sortable_runs for probe in montage.probes] == [(1, 2)]
    _pass(daemon_module, prefix, key)

    assert _rows(key) == stored


def _placed_and_listed(daemon, prefix, tmp_path_factory) -> dict:
    """A session placed with one montage and listed, no request yet accepted for it; returns its key."""
    key, serial = _landed(tmp_path_factory)
    _pass(daemon, prefix, key)
    put(key, [_position(serial, 1, before_t0(key))], seq=1, prefix=prefix)
    _pass(daemon, prefix, key)
    assert _derivation(key) == ("placed", 1) and [m[0] for m in _montages(key)] == [1]
    return key


def _request_row(key, idempotency_key: str) -> None:
    """The `Request` an `Activation` names, as `accept()` would have stored it."""
    from wl_preproc.schema import request

    request.Request.insert1({"idempotency_key": idempotency_key, "task_type": "neural", "origin": "wl_works",
                             "payload": {}, "requested_by": "jw",
                             "requested_at": datetime.datetime(2027, 1, 1, 9, 0)})


def _rederived():
    from wl_preproc.montage.stored import StoredMontages

    return StoredMontages(status="waiting", positions_seq=2, flags=(), montages=(), groups=())


def test_a_session_given_an_accepted_request_before_its_write_is_not_rewritten(
        daemon_module, prefix, tmp_path_factory):
    """D8 at the write itself: the stage checks for an accepted request before it derives, and a request can be
    accepted between that check and the write. `write()` checks again under the derivation's lock, and writes
    nothing."""
    from wl_preproc.montage.stored import write
    from wl_preproc.schema import request

    key = _placed_and_listed(daemon_module, prefix, tmp_path_factory)
    _request_row(key, f"{key['subject']}-k1")
    request.Activation.insert1({**key, "montage_id": 1, "activation_id": 0, "role": "canonical",
                                "request_key": f"{key['subject']}-k1",
                                "created_at": datetime.datetime(2027, 1, 1, 9, 0)})
    stored = _rows(key)

    assert write(key, _rederived()) is False
    assert _rows(key) == stored


def test_a_request_accepted_while_the_write_waits_for_its_lock_is_seen(daemon_module, prefix, tmp_path_factory,
                                                                       monkeypatch):
    """The same race with real timing: the request's transaction commits on another connection after the write's
    transaction has begun and before it holds the derivation's lock, as when `accept()` held that lock first. A
    transaction reading from the snapshot it began with would not see the activation, and would try to rewrite the
    session under it."""
    from tests.schema.test_request import _raw_connection
    from wl_preproc.montage import stored as stored_module
    from wl_preproc.schema import request

    key = _placed_and_listed(daemon_module, prefix, tmp_path_factory)
    _request_row(key, f"{key['subject']}-k1")
    before = _rows(key)
    locked = stored_module.lock_derivation

    def accepted_meanwhile(session_key):
        connection = _raw_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {request.Activation.full_table_name} (subject, session_datetime, montage_id, "
                    "activation_id, role, request_key, created_at) VALUES (%s, %s, 1, 0, 'canonical', %s, %s)",
                    (key["subject"], key["session_datetime"], f"{key['subject']}-k1",
                     datetime.datetime(2027, 1, 1, 9, 0)))
        finally:
            connection.close()
        return locked(session_key)

    monkeypatch.setattr(stored_module, "lock_derivation", accepted_meanwhile)
    assert stored_module.write(key, _rederived()) is False
    assert _rows(key) == before


def _job(key, idempotency_key: str, **selection):
    """wl.works' request naming montage 1, asserting every run, with no probes."""
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle

    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
             "works_run_id": f"wr-{row['run_number']}"} for row in _runs(key)]
    return JobRequest(
        domain="neural", parameters={}, idempotency_key=idempotency_key,
        selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC), "montage_id": 1,
                   **selection},
        metadata=MetadataBundle(runs=runs, probes=[], experimenter="jw", subject=key["subject"], task_types=[]))


def test_a_seq_recorded_alone_after_a_request_is_listed_and_requests_go_on(daemon_module, prefix, tmp_path_factory):
    """D2: after the first request, the same set at a higher seq records the seq alone. The stage never derives the
    session again (D8), so the derivation takes the seq with it: `GET /sessions` lists it, and a request is not
    refused for positions its montages were not derived from."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import request

    key, serial = _landed(tmp_path_factory)
    _pass(daemon_module, prefix, key)
    positions = [_position(serial, 1, before_t0(key))]
    put(key, positions, seq=1, prefix=prefix)
    _pass(daemon_module, prefix, key)
    first = accept(_job(key, f"{key['subject']}-k1"), prefix=prefix)
    montages = _rows(key)

    assert put(key, positions, seq=2, prefix=prefix) == {"positions_seq": 2, "changed": False}
    _pass(daemon_module, prefix, key)

    (entry,) = [e for e in list_sessions(None, prefix=prefix)["sessions"] if e["subject"] == key["subject"]]
    assert (entry["positions_status"], entry["positions_seq"]) == ("placed", 2)
    derivative = accept(_job(key, f"{key['subject']}-k2", run_numbers=[1]), prefix=prefix)
    assert (request.Activation & derivative).fetch1("role") == "derivative"
    assert accept(_job(key, f"{key['subject']}-k1"), prefix=prefix) == first
    # Nothing but the seq moved.
    after = _rows(key)
    assert [{k: v for k, v in row.items() if k != "positions_seq"} for row in after.pop("MontageDerivation")] == [
        {k: v for k, v in row.items() if k != "positions_seq"} for row in montages.pop("MontageDerivation")]
    assert after == montages


def test_an_empty_set_after_a_request_on_a_session_without_ephys_leaves_requests_accepted(
        daemon_module, prefix, tmp_path_factory):
    """The other route to a seq the derivation never had: a session without ephys is requested with no positions on
    file, and an empty set arrives after. Its derivation takes the seq, and the next request is accepted."""
    from wl_preproc.responder.jobs import accept

    key, _serial = _landed(tmp_path_factory, systems=("syncbox",))
    _pass(daemon_module, prefix, key)
    assert _derivation(key) == ("not_needed", None)
    first = accept(_job(key, f"{key['subject']}-k1"), prefix=prefix)

    assert put(key, [], seq=1, prefix=prefix) == {"positions_seq": 1, "changed": True}

    assert _derivation(key) == ("not_needed", 1)
    assert accept(_job(key, f"{key['subject']}-k2", run_numbers=[1]), prefix=prefix) != first


def test_the_derivation_changes_only_when_its_result_does(daemon_module, prefix, tmp_path_factory):
    from wl_preproc.montage.stage import derive_session, run_stage

    key, serial = _landed(tmp_path_factory)
    put(key, [_position(serial, 1, key["session_datetime"] - datetime.timedelta(minutes=10))], seq=1,
        prefix=prefix)
    _pass(daemon_module, prefix, key)
    stored = _rows(key)
    assert stored["MontageDerivation"][0]["positions_status"] == "placed"

    _pass(daemon_module, prefix, key)

    assert _rows(key) == stored
    assert derive_session(key) is False
    written, _errors = run_stage()
    assert written == 0


def test_serials_that_sql_and_python_order_differently_are_written_once(daemon_module, prefix, tmp_path_factory):
    """The montages review's M1: MySQL orders "a1" before "B2" (without case), Python after. The stored probes are
    ordered as the derivation's are, so a second derivation finds them equal and writes nothing."""
    from wl_preproc.montage.stage import derive_session
    from wl_preproc.montage.stored import read_stored

    key, serial = _landed(tmp_path_factory)
    _pass(daemon_module, prefix, key)
    put(key, [_position(serial, 1, before_t0(key)), _position("a1", 2, before_t0(key)),
              _position("B2", 3, before_t0(key))], seq=1, prefix=prefix)
    _pass(daemon_module, prefix, key)
    assert _derivation(key) == ("placed", 1)

    (montage,) = read_stored([key])[(key["subject"], key["session_datetime"])].montages
    assert [probe["serial"] for probe in montage["probes"]] == sorted([serial, "a1", "B2"])
    assert derive_session(key) is False


def test_a_session_with_intan_and_no_spikeglx_is_placed_once_its_positions_arrive(
        daemon_module, prefix, tmp_path_factory):
    """Plan decision D7: Intan is ephys, so the session waits for positions; with no SpikeGLX there is no census to
    wait for. An Intan probe is never in the census (D10): it is listed with no selection, sortable on the runs its
    position covers. A session of Intan alone, with no sync box, has no events stage to give it runs or t = 0, so
    this is the standalone Intan rig's session, the sync box beside it."""
    from wl_preproc.schema import core

    key, _serial = _landed(tmp_path_factory, systems=("syncbox", "rhs"))
    _pass(daemon_module, prefix, key)
    assert _derivation(key) == ("waiting", None)
    assert not core.Montage & key

    intan = new_serial()
    put(key, [_position(intan, 1, before_t0(key))], seq=1, prefix=prefix)
    _pass(daemon_module, prefix, key)

    assert _derivation(key) == ("placed", 1)
    first, second = _runs(key)
    assert _montages(key) == [(1, first["run_start_time"], second["run_stop_time"])]
    assert _probes(key) == {(1, intan): (1, None, [1, 2])}
    assert (core.MontageProbe & key).fetch1("probe_type") is None
    placed = (core.PlacedPosition & key).fetch1()
    assert (placed["probe_serial"], placed["start_s"], placed["end_s"]) == (
        intan, first["run_start_time"], second["run_stop_time"])

"""`PUT /positions` stored (design spec `2026-11-24-montages-design.md` section 4.1, plan decisions D1, D2)."""

from __future__ import annotations

import datetime

import pytest

from tests.positions import put, seed_montage

_AT = datetime.datetime(2027, 6, 1, 9, 0)


@pytest.fixture
def landed_session(dj_conn, prefix):
    """A `(subject, session_datetime)` with Lab/Subject/Session on file; a copy of
    `tests/responder/test_jobs.py`'s fixture, with its runs (measured) when given."""
    from wl_preproc.schema import core, pipeline
    from wl_preproc.schema import request as schema_request

    schema_request.activate(prefix=prefix)

    def _land(subject: str, session_datetime: datetime.datetime, runs=((1, 0.0, 4.0), (2, 5.0, 11.0))) -> dict:
        pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "W", "address": "y", "time_zone": "UTC"},
                                 skip_duplicates=True)
        pipeline.subject.Subject.insert1(
            {"subject": subject, "sex": "M", "subject_birth_date": datetime.date(2020, 1, 1),
             "subject_description": ""}, skip_duplicates=True)
        key = {"subject": subject, "session_datetime": session_datetime}
        pipeline.Session.insert1(key, skip_duplicates=True)
        if runs:
            core.Run.insert([{**key, "run_number": n, "task_type": 0, "run_start_time": a, "run_stop_time": b,
                              "closed": 1} for n, a, b in runs], skip_duplicates=True)
        return key

    return _land


def _position(number: int = 1, serial: str = "19011110001", start=_AT, **more) -> dict:
    return {"serial": serial, "insertion_number": number, "inserted_at": start, **more}


def _two() -> list[dict]:
    return [_position(1, start=_AT, ended_at=_AT + datetime.timedelta(hours=1), end_kind="moved",
                      ended_after_run=1),
            _position(2, start=_AT + datetime.timedelta(hours=1, microseconds=500))]


def _stored(key):
    from wl_preproc.schema import request

    master = (request.PositionSet & key).fetch1()
    rows = (request.PositionSet.Position & key).to_dicts()
    return master, sorted(rows, key=lambda r: r["insertion_number"])


def test_a_first_set_is_stored(landed_session, prefix):
    key = landed_session("pstfirst", _AT)
    assert put(key, _two(), seq=1, prefix=prefix) == {"positions_seq": 1, "changed": True}
    master, rows = _stored(key)
    assert master["positions_seq"] == 1
    assert [(r["insertion_number"], r["probe_serial"], r["inserted_at"], r["ended_at"], r["end_kind"],
             r["inserted_after_run"], r["ended_after_run"]) for r in rows] == [
        (1, "19011110001", _AT, _AT + datetime.timedelta(hours=1), "moved", None, 1),
        (2, "19011110001", _AT + datetime.timedelta(hours=1, microseconds=500), None, None, None, None)]


def test_a_higher_seq_replaces_the_set(landed_session, prefix):
    key = landed_session("pstmore", _AT)
    put(key, _two(), seq=1, prefix=prefix)
    assert put(key, [_position(1), ], seq=2, prefix=prefix) == {"positions_seq": 2, "changed": True}
    master, rows = _stored(key)
    assert (master["positions_seq"], [r["insertion_number"] for r in rows]) == (2, [1])
    put(key, [_position(1), _position(3, serial="2", start=_AT)], seq=3, prefix=prefix)
    assert [r["insertion_number"] for r in _stored(key)[1]] == [1, 3]


def test_a_stale_seq_is_a_409_carrying_the_stored_one(landed_session, prefix):
    from wl_preproc.responder.handler import PositionsConflict

    key = landed_session("pststale", _AT)
    put(key, _two(), seq=5, prefix=prefix)
    with pytest.raises(PositionsConflict) as raised:
        put(key, [_position(1)], seq=4, prefix=prefix)
    assert raised.value.positions_seq == 5
    assert "4" in str(raised.value) and "5" in str(raised.value)
    master, rows = _stored(key)
    assert (master["positions_seq"], len(rows)) == (5, 2)


def test_an_equal_seq_with_the_same_set_is_a_no_op(landed_session, prefix):
    key = landed_session("pstsame", _AT)
    put(key, _two(), seq=1, prefix=prefix)
    received = _stored(key)[0]["received_at"]
    assert put(key, list(reversed(_two())), seq=1, prefix=prefix) == {"positions_seq": 1, "changed": False}
    assert _stored(key)[0]["received_at"] == received


def test_an_equal_seq_with_another_set_is_a_409(landed_session, prefix):
    from wl_preproc.responder.handler import PositionsConflict

    key = landed_session("pstother", _AT)
    put(key, _two(), seq=3, prefix=prefix)
    with pytest.raises(PositionsConflict) as raised:
        put(key, [_position(1)], seq=3, prefix=prefix)
    assert raised.value.positions_seq == 3
    assert len(_stored(key)[1]) == 2


def test_aware_times_are_the_same_set(landed_session, prefix):
    """Review Focus 1: the same instants, however written, are the same set."""
    utc = datetime.timezone.utc
    plus_one = datetime.timezone(datetime.timedelta(hours=1))
    key = landed_session("pstaware", _AT)

    def shifted(zone):
        return [{**p, **{f: (p[f].replace(tzinfo=utc).astimezone(zone) if zone else p[f])
                         for f in ("inserted_at", "ended_at") if p.get(f) is not None}} for p in _two()]

    assert put(key, shifted(utc), seq=1, prefix=prefix)["changed"] is True
    assert put(key, shifted(None), seq=1, prefix=prefix) == {"positions_seq": 1, "changed": False}
    assert put(key, shifted(plus_one), seq=1, prefix=prefix) == {"positions_seq": 1, "changed": False}
    assert [r["inserted_at"] for r in _stored(key)[1]][0] == _AT


def test_a_session_not_landed_is_a_404(dj_conn, prefix):
    from wl_preproc.responder.handler import NotFoundError
    from wl_preproc.schema import request

    request.activate(prefix=prefix)
    key = {"subject": "pstnone", "session_datetime": _AT}
    with pytest.raises(NotFoundError) as raised:
        put(key, [_position(1)], prefix=prefix)
    assert "pstnone" in str(raised.value) and _AT.isoformat() in str(raised.value)
    assert len(request.PositionSet & key) == 0


def test_after_the_first_request_the_set_is_fixed(landed_session, prefix):
    """Section 4.1 and D2: a changed set is refused, naming the canonical's key; the same set records the
    seq alone."""
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
    from wl_preproc.responder.handler import PositionsConflict
    from wl_preproc.responder.jobs import accept

    key = landed_session("pstfixed", _AT)
    put(key, _two(), seq=2, prefix=prefix)
    seed_montage(key, {"montage_id": 0, "start_s": 0.0, "end_s": 12.0})
    job = JobRequest(
        domain="neural", selection={"session_datetime": _AT.replace(tzinfo=datetime.UTC), "montage_id": 0},
        parameters={}, idempotency_key="pstfixed-k1",
        metadata=MetadataBundle(
            runs=[{"run_number": n, "start_s": a, "end_s": b, "works_run_id": f"wr-{n}"}
                  for n, a, b in ((1, 0.0, 4.0), (2, 5.0, 11.0))],
            probes=[], experimenter="jw", subject="pstfixed", task_types=[], subject_details=None))
    accept(job, prefix=prefix)

    with pytest.raises(PositionsConflict) as raised:
        put(key, [_position(1)], seq=9, prefix=prefix)
    assert raised.value.positions_seq == 2 and "pstfixed-k1" in str(raised.value)

    assert put(key, _two(), seq=9, prefix=prefix) == {"positions_seq": 9, "changed": False}
    master, rows = _stored(key)
    assert (master["positions_seq"], len(rows)) == (9, 2)

    with pytest.raises(PositionsConflict) as stale:
        put(key, _two(), seq=8, prefix=prefix)
    assert stale.value.positions_seq == 9


def test_before_t0_is_the_stored_reference_floored_less_minutes(dj_conn, prefix, tmp_path_factory):
    """The helper later tasks place positions with: `ClockReference.reference_time` floored to its minute."""
    from tests.identities import new_animal
    from tests.positions import before_t0
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon
    from wl_preproc.schema import timebase

    daemon.activate_all(prefix=prefix)
    session = new_animal().session()
    _recipe, key = _session(tmp_path_factory, subject=session.subject, session_id=session.session_id,
                            session_datetime=session.session_datetime, runs=True)
    daemon.run_once(prefix=prefix)
    reference = (timebase.ClockReference & key).fetch1("reference_time")
    assert before_t0(key, 3) == reference.replace(second=0, microsecond=0) - datetime.timedelta(minutes=3)
    assert before_t0(key).second == 0

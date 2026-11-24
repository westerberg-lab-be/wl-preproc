"""What `PUT /positions` does (design spec `2026-11-24-montages-design.md`
section 4.1; plan decisions D1, D2).

`handler.py` owns HTTP and imports no DataJoint; this module owns the
database, as `nwb.py` does, and `server.py` runs it under the responder's one
lock. It stores the set wl.works sent and derives nothing: the montages come
from the daemon's next pass."""

from __future__ import annotations

import datetime

from wl_preproc.contracts.protocol import PositionsRequest, PositionsResponse, as_naive_utc
from wl_preproc.responder.handler import NotFoundError, PositionsConflict
from wl_preproc.schema import DEFAULT_PREFIX

_FIELDS = ("insertion_number", "serial", "inserted_at", "ended_at", "end_kind", "inserted_after_run",
           "ended_after_run")


def _naive(value: datetime.datetime | None) -> datetime.datetime | None:
    return None if value is None else as_naive_utc(value)


def _set_of(positions: list[dict]) -> list[tuple]:
    """The set as the sorted list of each position's seven fields, times naive UTC, so the same instants
    written another way are the same set."""
    return sorted(
        (int(p["insertion_number"]), p["serial"], _naive(p["inserted_at"]), _naive(p["ended_at"]), p["end_kind"],
         p["inserted_after_run"], p["ended_after_run"]) for p in positions)


def _as_stored(row: dict) -> dict:
    """A stored `Position` row in `ProbePosition`'s shape."""
    return {"serial": row["probe_serial"], "insertion_number": int(row["insertion_number"]),
            "inserted_at": row["inserted_at"], "ended_at": row["ended_at"], "end_kind": row["end_kind"],
            "inserted_after_run": row["inserted_after_run"], "ended_after_run": row["ended_after_run"]}


def stored_positions(session_key: dict, prefix: str | None = None) -> tuple[int, list[dict]] | None:
    """The stored seq and the positions as `ProbePosition`-shaped dicts (naive UTC times) by insertion number,
    or None when none has been stored for the session.

    It activates `request` under `prefix` when given one. Without one it reads the schemas as already
    activated, as the daemon's montage stage calls it: activating the default prefix there would fail in a
    process bound to another."""
    from wl_preproc.schema import request

    if prefix is not None:
        request.activate(prefix=prefix)
    key = {"subject": session_key["subject"], "session_datetime": session_key["session_datetime"]}
    master = (request.PositionSet & key).to_dicts()
    if not master:
        return None
    rows = sorted((request.PositionSet.Position & key).to_dicts(), key=lambda row: row["insertion_number"])
    return int(master[0]["positions_seq"]), [_as_stored(row) for row in rows]


def put_positions(request: PositionsRequest, prefix: str = DEFAULT_PREFIX) -> dict:
    """Store the session's whole set, or refuse it; returns `PositionsResponse` as a dict.

    In order: a session not landed is a `NotFoundError`; a seq below the stored one, or an equal seq with
    another set, is a `PositionsConflict`; once the session has an accepted request a changed set is a
    `PositionsConflict` naming it, and the same set records the seq alone (D2); otherwise the parts are
    replaced and the master updated in one transaction.

    **After the first accepted request, a seq recorded alone is the derivation's too.** The stage never derives
    such a session again (D8), so its `MontageDerivation.positions_seq` would stay behind the stored seq for good,
    and `responder/jobs.py::accept` refuses every request whose positions are not the seq its montages were derived
    from. The set is the same, so the montages are what that seq derives: the seq is written on the derivation's
    row in the same transaction, and nothing else on it or any other montage row. So is the seq of an empty set
    sent after a request was accepted with none on file.

    **The derivation's row is taken first**, `FOR UPDATE`, as `accept()` takes it, before anything reads or writes
    the positions: the two take their locks in one order (derivation, then positions) and never interleave. The
    reads after it are in `READ COMMITTED`, as `montage/stored.py::write`'s are, so a request accepted while this
    waited for the lock is seen, not missed in a snapshot taken before it."""
    import datajoint as dj

    from wl_preproc.montage import stored as stored_montages
    from wl_preproc.schema import core, pipeline
    from wl_preproc.schema import request as schema_request

    schema_request.activate(prefix=prefix)
    moment = as_naive_utc(request.session_datetime)
    key = {"subject": request.subject, "session_datetime": moment}
    if not pipeline.Session & key:
        raise NotFoundError(f"no session of subject {request.subject!r} at {moment.isoformat()} has landed here")
    sent = [position.model_dump() for position in request.positions]
    seq = request.positions_seq
    received = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    connection = dj.conn()
    # For this one transaction. A reconnect before it begins loses it, and the reads fall back to the snapshot.
    connection.query("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    with connection.transaction:
        derivation = stored_montages.lock_derivation(key)
        current = stored_positions(key)
        first = schema_request.first_accepted_request(key)
        if current is None:
            stored_seq, same = None, False
        else:
            stored_seq = current[0]
            same = _set_of(current[1]) == _set_of(sent)
            if seq < stored_seq:
                raise PositionsConflict(
                    f"positions_seq {seq} is below the {stored_seq} this host holds", stored_seq)
            if seq == stored_seq:
                if not same:
                    raise PositionsConflict(
                        f"positions_seq {seq} is the one this host holds, with another set of positions", stored_seq)
                return PositionsResponse(positions_seq=stored_seq, changed=False).model_dump()
            if first is not None and not same:
                raise PositionsConflict(
                    f"the positions are fixed by the request {first!r}; positions_seq {seq} changes them", stored_seq)
        if current is None and first is not None:
            # A request accepted with no positions on file (a session without ephys): the set is fixed as empty,
            # and there is no stored seq to carry, so the 409 says 0.
            if sent:
                raise PositionsConflict(
                    f"the positions are fixed by the request {first!r}; positions_seq {seq} adds them", 0)
        if current is not None and same:
            schema_request.PositionSet.update1({**key, "positions_seq": seq, "received_at": received})
        else:
            (schema_request.PositionSet.Position & key).delete_quick()
            if current is None:
                schema_request.PositionSet.insert1({**key, "positions_seq": seq, "received_at": received})
            else:
                schema_request.PositionSet.update1({**key, "positions_seq": seq, "received_at": received})
            schema_request.PositionSet.Position.insert([
                {**key, "insertion_number": p["insertion_number"], "probe_serial": p["serial"],
                 "inserted_at": _naive(p["inserted_at"]), "ended_at": _naive(p["ended_at"]),
                 "end_kind": p["end_kind"], "inserted_after_run": p["inserted_after_run"],
                 "ended_after_run": p["ended_after_run"]} for p in sent])
        if first is not None and derivation is not None:
            # The set is fixed (above), so these are the montages this seq derives (the docstring).
            core.MontageDerivation.update1({**key, "positions_seq": seq})
    return PositionsResponse(positions_seq=seq, changed=not (current is not None and same)).model_dump()

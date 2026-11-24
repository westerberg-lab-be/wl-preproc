"""Shared helpers for tests that store probe positions (`PUT /positions`)."""

from __future__ import annotations

import datetime

_MINUTE = datetime.timedelta(minutes=1)


def put(key: dict, positions: list[dict], seq: int = 1, prefix: str = "t_") -> dict:
    """`responder.positions.put_positions` for the session `key`, sending `positions` at `seq`."""
    from wl_preproc.contracts.protocol import PositionsRequest
    from wl_preproc.responder.positions import put_positions

    request = PositionsRequest.model_validate({
        "subject": key["subject"], "session_datetime": key["session_datetime"],
        "positions_seq": seq, "positions": positions,
    })
    return put_positions(request, prefix=prefix)


def before_t0(key: dict, minutes: int = 10) -> datetime.datetime:
    """The session's stored `ClockReference.reference_time`, floored to its minute, less `minutes` (naive UTC)."""
    from wl_preproc.schema import timebase

    reference = (timebase.ClockReference & key).fetch1("reference_time")
    return reference.replace(second=0, microsecond=0) - minutes * _MINUTE

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


def seed_montage(key: dict, montage: dict, probes: list[dict] = (), status: str = "placed") -> None:
    """Store `montage` ({montage_id, start_s, end_s}) for the session `key` as the montage stage would list it:
    its `core.Montage` row, a `MontageProbe` and its `MontageSortableRun` rows for each of `probes` ({serial,
    insertion_number, sortable_runs, and optionally electrode_config_hash and probe_type}), and the session's
    `MontageDerivation` at `status`, inserted or updated, derived from the stored positions' seq when there is one.
    A montage row already stored is kept.

    Call it after the session's last daemon pass and its last `PUT /positions` before `accept()`: until a request
    is accepted, a pass derives the session again and replaces whatever does not match its own result, this
    montage with it."""
    from wl_preproc.responder.positions import stored_positions
    from wl_preproc.schema import core

    session = {"subject": key["subject"], "session_datetime": key["session_datetime"]}
    derivation = {**session, "positions_status": status,
                  "derived_at": datetime.datetime.now(datetime.UTC).replace(tzinfo=None)}
    positions = stored_positions(session)
    if positions is not None:
        derivation["positions_seq"] = positions[0]
    if core.MontageDerivation & session:
        core.MontageDerivation.update1(derivation)
    else:
        core.MontageDerivation.insert1(derivation)
    where = {**session, "montage_id": montage["montage_id"]}
    core.Montage.insert1({**where, "start_s": montage["start_s"], "end_s": montage["end_s"]}, skip_duplicates=True)
    core.MontageProbe.insert([
        {**where, "probe_serial": probe["serial"], "insertion_number": probe["insertion_number"],
         "electrode_config_hash": probe.get("electrode_config_hash"), "probe_type": probe.get("probe_type")}
        for probe in probes], skip_duplicates=True)
    core.MontageSortableRun.insert([{**where, "probe_serial": probe["serial"], "run_number": run}
                                    for probe in probes for run in probe["sortable_runs"]], skip_duplicates=True)


def before_t0(key: dict, minutes: int = 10) -> datetime.datetime:
    """The session's stored `ClockReference.reference_time`, floored to its minute, less `minutes` (naive UTC)."""
    from wl_preproc.schema import timebase

    reference = (timebase.ClockReference & key).fetch1("reference_time")
    return reference.replace(second=0, microsecond=0) - minutes * _MINUTE


def land(tmp_path_factory, *, systems: tuple[str, ...], **update) -> dict:
    """`tests/schema/test_spikeglx_restart.py::_session` with `runs=True`, recording and landing `systems`
    (`("syncbox",)` for a session without ephys). `update` edits the CI recipe, and may name the
    `session_datetime` to land at (else 09:00 on the `session_id`'s date). Returns the session key."""
    from tests.schema.test_eye_populate import _land
    from wl_preproc.synth.recipe import CI_RECIPE, SessionRecipe
    from wl_preproc.synth.session import generate_session

    when = update.pop("session_datetime", None)
    recipe = SessionRecipe.model_validate(
        {**CI_RECIPE.model_dump(), "systems": list(systems), "runs": True, **update})
    root = tmp_path_factory.mktemp(recipe.subject)
    generate_session(root, recipe)
    when = when or datetime.datetime.fromisoformat(recipe.session_id[:10]).replace(hour=9)
    return _land(root, recipe, when, acquisition_systems=tuple(systems))

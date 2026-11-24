"""The daemon's montage stage (plan decisions D6-D8): each listable session's montages and shared groups, derived
from what is stored and written to `core` only when the result changes, until the session's first accepted request.

`gather_input` reads the facts the pure `derive` takes; `to_stored` turns a derivation and its groups into rows,
minting each group's electrodes as an `ephys.ElectrodeConfig`. A session with an accepted request is never derived
again: its positions are fixed (spec section 4.1), and its runs and census do not change once landed, so what a stored
activation covers is never changed under it (spec section 5).
"""

from __future__ import annotations

from wl_preproc.listing import order
from wl_preproc.montage.derive import Derivation, DerivationInput, PositionIn, RunSelection, derive
from wl_preproc.montage.edges import RunSpan
from wl_preproc.montage.groups import DerivedGroup, shared_groups
from wl_preproc.montage.stored import StoredMontages, read_stored, write

_SESSION = ("subject", "session_datetime")
_EPHYS_SYSTEMS = ("spikeglx", "rhs")
# `MontageDerivation.Flag.message`'s width. A longer message (an edge whose window touches many short runs' gaps) is
# cut to fit, rather than failing the session's insert on every pass.
_MESSAGE_MAX = 1024


def _key(session_key: dict) -> dict:
    return {name: session_key[name] for name in _SESSION}


def _int(value) -> int | None:
    return None if value is None else int(value)


def _census_complete(key: dict, systems: set[str]) -> bool:
    """D7's SpikeGLX census: the clock fit attempted, every segment censused, and at least one file found, or none
    recorded. A session with no SpikeGLX system has no census to wait for."""
    from wl_preproc.schema import core, ephys, timebase

    if "spikeglx" not in systems:
        return True
    spikeglx = {**key, "system": "spikeglx"}
    fits = (timebase.SystemTimebase & spikeglx).to_arrays("fit_status")
    if not len(fits):
        return False
    if fits[0] == "no_recording":
        return True
    segments = core.Segment & spikeglx
    if not segments and not core.RejectedSegment & spikeglx:
        return False
    return not segments - ephys.ProbeCensus


def _selections(key: dict, runs: tuple[RunSpan, ...]) -> tuple[dict[tuple[int, str], RunSelection], frozenset[str]]:
    """D6: each run's selection per serial, from the first and the last SpikeGLX segment (by start) that overlaps
    the run and places that serial's sites; and every serial the census names."""
    from wl_preproc.schema import core, ephys

    spikeglx = {**key, "system": "spikeglx"}
    segments = {row["segment_id"]: (float(row["start_s"]), float(row["end_s"]))
                for row in (core.Segment & spikeglx).proj("start_s", "end_s").to_dicts()
                if not order.ends_before_start(row["start_s"], row["end_s"])}
    parts = (ephys.ProbeCensus.Probe & spikeglx).to_dicts(order_by=["segment_id", "stream"])
    recorded = frozenset(str(part["probe_serial"]) for part in parts if part["probe_serial"])
    placed: dict[str, list[tuple[float, float, str, str]]] = {}
    for part in parts:
        if part["probe_serial"] and part["electrode_config_hash"] and part["segment_id"] in segments:
            start, end = segments[part["segment_id"]]
            placed.setdefault(str(part["probe_serial"]), []).append(
                (start, end, str(part["segment_id"]), str(part["electrode_config_hash"])))
    selections = {}
    for run in runs:
        for serial, found in placed.items():
            # As the listing spans a run with segments: each overlaps it.
            over = sorted(s for s in found if s[0] < run.end_s and s[1] > run.start_s)
            if over:
                selections[(run.run_number, serial)] = RunSelection(over[0][3], over[-1][3])
    return selections, recorded


def gather_input(session_key: dict) -> DerivationInput:
    """The facts `derive` takes for the session, read from the database (D6, D7)."""
    from wl_preproc.responder.positions import stored_positions
    from wl_preproc.schema import core, timebase

    key = _key(session_key)
    runs = tuple(sorted(
        (RunSpan(int(row["run_number"]), float(row["run_start_time"]), float(row["run_stop_time"]))
         for row in (core.Run & key).to_dicts()
         if not order.ends_before_start(row["run_start_time"], row["run_stop_time"])),
        key=lambda run: (run.start_s, run.run_number)))
    stored = stored_positions(key)
    positions = None if stored is None else tuple(
        PositionIn(serial=p["serial"], insertion_number=int(p["insertion_number"]), inserted_at=p["inserted_at"],
                   ended_at=p["ended_at"], end_kind=p["end_kind"], inserted_after_run=_int(p["inserted_after_run"]),
                   ended_after_run=_int(p["ended_after_run"]))
        for p in stored[1])
    references = (timebase.ClockReference & key).to_dicts()
    systems = {str(system) for system in (core.AcquisitionSystem & key).to_arrays("system")}
    selections, recorded = _selections(key, runs)
    return DerivationInput(
        has_ephys=bool(systems & set(_EPHYS_SYSTEMS)),
        runs=runs,
        positions=positions,
        positions_seq=None if stored is None else stored[0],
        t0=references[0]["reference_time"] if references else None,
        t0_source=str(references[0]["reference_source"]) if references else None,
        census_complete=_census_complete(key, systems),
        selections=selections,
        recorded_serials=recorded,
    )


def _probe_types(hashes: set[str]) -> dict[str, str]:
    from wl_preproc.schema import ephys

    if not hashes:
        return {}
    rows = (ephys.ElectrodeConfig & [{"electrode_config_hash": h} for h in sorted(hashes)]).to_dicts()
    return {str(row["electrode_config_hash"]): str(row["probe_type"]) for row in rows}


def _fitted(message: str) -> str:
    return message if len(message) <= _MESSAGE_MAX else message[:_MESSAGE_MAX - 3] + "..."


def to_stored(derivation: Derivation, groups: tuple[DerivedGroup, ...], runs: tuple[RunSpan, ...]) -> StoredMontages:
    """The rows `derivation` and its `groups` are stored as. `runs` are the derivation's own (its input's), which
    place each position between its first covered run's start and its last's end. Each group's electrodes are
    minted as an `ElectrodeConfig` (registering it, idempotently), unless its probe has no channel map read here."""
    from wl_preproc.schema import ephys

    hashes = {p.electrode_config_hash for m in derivation.montages for p in m.probes if p.electrode_config_hash}
    types = _probe_types(hashes)
    montages = tuple(
        {"montage_id": m.montage_id, "start_s": m.start_s, "end_s": m.end_s, "probes": tuple(
            {"serial": p.serial, "insertion_number": p.insertion_number,
             "electrode_config_hash": p.electrode_config_hash,
             "probe_type": types[p.electrode_config_hash] if p.electrode_config_hash else None,
             "sortable_runs": tuple(sorted(p.sortable_runs))}
            for p in sorted(m.probes, key=lambda p: p.serial))}
        for m in sorted(derivation.montages, key=lambda m: m.montage_id))

    stored_groups = []
    for g in sorted(groups, key=lambda g: g.group_id):
        config_hash = probe_type = n_electrodes = None
        if g.member_hashes:
            config_hash = ephys.intersect_electrode_configs(list(g.member_hashes))
            config = (ephys.ElectrodeConfig & {"electrode_config_hash": config_hash}).fetch1()
            probe_type, n_electrodes = str(config["probe_type"]), int(config["n_electrodes"])
            if n_electrodes != len(g.electrodes):  # raised, not asserted: `python -O` strips an assert
                raise AssertionError(
                    f"group {g.group_id}'s minted config {config_hash} holds {n_electrodes} electrodes, not the "
                    f"{len(g.electrodes)} its members share")
        stored_groups.append({
            "group_id": g.group_id, "serial": g.serial, "insertion_number": g.insertion_number,
            "electrode_config_hash": config_hash, "probe_type": probe_type, "n_electrodes": n_electrodes,
            "montage_ids": tuple(sorted(g.montage_ids)), "sortable_runs": tuple(sorted(g.sortable_runs))})

    spans = {run.run_number: run for run in runs}
    placed = tuple(
        {"serial": p.serial, "insertion_number": p.insertion_number,
         "start_s": spans[p.covered_runs[0]].start_s if p.covered_runs else None,
         "end_s": spans[p.covered_runs[-1]].end_s if p.covered_runs else None}
        for p in sorted(derivation.placed, key=lambda p: p.insertion_number))
    flags = tuple({"code": f["code"], "message": _fitted(f["message"]), "run_number": f["run_number"],
                   "block_number": None, "segment_id": None} for f in derivation.flags)
    return StoredMontages(status=derivation.status, positions_seq=derivation.positions_seq, flags=flags,
                          montages=montages, groups=tuple(stored_groups), placed=placed)


def _electrodes_of(derivation: Derivation) -> dict[str, frozenset[int]]:
    """Each of the montages' hashes' electrodes, from `ElectrodeConfig.Electrode`."""
    from wl_preproc.schema import ephys

    hashes = sorted({p.electrode_config_hash for m in derivation.montages for p in m.probes
                     if p.electrode_config_hash})
    if not hashes:
        return {}
    electrodes: dict[str, set[int]] = {h: set() for h in hashes}
    for row in (ephys.ElectrodeConfig.Electrode & [{"electrode_config_hash": h} for h in hashes]).to_dicts():
        electrodes[str(row["electrode_config_hash"])].add(int(row["electrode"]))
    return {h: frozenset(found) for h, found in electrodes.items()}


def derive_session(session_key: dict) -> bool:
    """Derive the session and write its rows when they differ from the stored ones (`derived_at` aside). True when
    it wrote. A session with an accepted request is left as it is (D8)."""
    from wl_preproc.schema import request

    key = _key(session_key)
    if request.first_accepted_request(key) is not None:
        return False
    facts = gather_input(key)
    derivation = derive(facts)
    groups = shared_groups(derivation.montages, _electrodes_of(derivation))
    stored = to_stored(derivation, groups, facts.runs)
    if read_stored([key]).get((key["subject"], key["session_datetime"])) == stored:
        return False
    write(key, stored)
    return True


def run_stage() -> tuple[int, list[str]]:
    """Derive every listable session. Returns `(sessions written, per-session failures)`, as every stage of
    `daemon.run_once` does."""
    from wl_preproc.listing.stage import listable

    try:
        keys = listable()
    except Exception as exc:  # the daemon's later stages must still run
        return 0, [f"montages: the sessions could not be read: {type(exc).__name__}: {exc}"]
    written, errors = 0, []
    for key in keys:
        try:
            written += derive_session(key)
        except Exception as exc:  # one session must not stop the others
            errors.append(f"montages {key}: {exc}")
    return written, errors

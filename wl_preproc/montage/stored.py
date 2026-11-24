"""A session's montages and shared groups as stored in `core` (plan decision D9): the one copy the listing, the
request checks and the NWB build read (spec section 5's "one definition"), never re-derived.

`StoredMontages` is what `stage.to_stored` builds from a derivation and what `read_stored` reads back, in one shape,
so the stage compares the two and rewrites a session only when they differ. `derived_at` is written, never compared.
"""

from __future__ import annotations

import collections
import datetime
from dataclasses import dataclass

_SESSION = ("subject", "session_datetime")


@dataclass(frozen=True)
class StoredMontages:
    status: str
    positions_seq: int | None
    flags: tuple[dict, ...]  # the listing's `_flag` shape, in the derivation's order
    # Each {montage_id, start_s, end_s, probes: ({serial, insertion_number, electrode_config_hash, probe_type,
    # sortable_runs},)}, by montage_id; probes by serial; runs ascending.
    montages: tuple[dict, ...]
    # Each {group_id, serial, insertion_number, electrode_config_hash, probe_type, n_electrodes, montage_ids,
    # sortable_runs}, by group_id; ids and runs ascending.
    groups: tuple[dict, ...]
    # Each {serial, insertion_number, start_s, end_s}, by insertion_number: `core.PlacedPosition`.
    placed: tuple[dict, ...] = ()


def _session(row: dict) -> tuple[str, datetime.datetime]:
    return row["subject"], row["session_datetime"]


def _int(value) -> int | None:
    return None if value is None else int(value)


def _float(value) -> float | None:
    return None if value is None else float(value)


def _str(value) -> str | None:
    return None if value is None else str(value)


def _by_session(rows: list[dict]) -> dict[tuple[str, datetime.datetime], list[dict]]:
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[_session(row)].append(row)
    return grouped


def read_stored(keys: list[dict]) -> dict[tuple[str, datetime.datetime], StoredMontages]:
    """What is stored for each of `keys`' sessions that has a `MontageDerivation`, by `(subject,
    session_datetime)`. Each table is read once for all of them, as `listing/entry.py::gather_all` reads."""
    from wl_preproc.schema import core

    keys = [{name: key[name] for name in _SESSION} for key in keys]
    if not keys:
        return {}
    masters = (core.MontageDerivation & keys).to_dicts()
    if not masters:
        return {}
    # Ordered here, never by ORDER BY: MySQL's collation orders strings without case ("a1" before "B2"), Python
    # with it, and `stage.to_stored` orders in Python. Two orders would never compare equal, and the session would
    # be rewritten on every pass (the montages review's M1).
    flags = _by_session(sorted((core.MontageDerivation.Flag & keys).to_dicts(), key=lambda r: int(r["flag_number"])))
    montages = _by_session(sorted((core.Montage & keys).to_dicts(), key=lambda r: int(r["montage_id"])))
    probes = _by_session(sorted((core.MontageProbe & keys).to_dicts(),
                                key=lambda r: (int(r["montage_id"]), str(r["probe_serial"]))))
    probe_runs = collections.defaultdict(list)
    for row in (core.MontageSortableRun & keys).to_dicts():
        probe_runs[(*_session(row), int(row["montage_id"]), row["probe_serial"])].append(int(row["run_number"]))
    placed = _by_session(sorted((core.PlacedPosition & keys).to_dicts(), key=lambda r: int(r["insertion_number"])))
    groups = _by_session(sorted((core.SharedGroup & keys).to_dicts(), key=lambda r: int(r["group_id"])))
    members = collections.defaultdict(list)
    for row in (core.SharedGroup.Member & keys).to_dicts():
        members[(*_session(row), int(row["group_id"]))].append(int(row["montage_id"]))
    group_runs = collections.defaultdict(list)
    for row in (core.SharedGroup.SortableRun & keys).to_dicts():
        group_runs[(*_session(row), int(row["group_id"]))].append(int(row["run_number"]))

    stored = {}
    for master in masters:
        session = _session(master)
        probes_of = collections.defaultdict(list)
        for row in probes.get(session, []):
            montage_id = int(row["montage_id"])
            probes_of[montage_id].append({
                "serial": str(row["probe_serial"]), "insertion_number": int(row["insertion_number"]),
                "electrode_config_hash": _str(row["electrode_config_hash"]), "probe_type": _str(row["probe_type"]),
                "sortable_runs": tuple(sorted(probe_runs[(*session, montage_id, row["probe_serial"])])),
            })
        stored[session] = StoredMontages(
            status=str(master["positions_status"]),
            positions_seq=_int(master["positions_seq"]),
            flags=tuple({"code": str(row["code"]), "message": str(row["message"]),
                         "run_number": _int(row["run_number"]), "block_number": None, "segment_id": None}
                        for row in flags.get(session, [])),
            montages=tuple({"montage_id": int(row["montage_id"]), "start_s": float(row["start_s"]),
                            "end_s": float(row["end_s"]), "probes": tuple(probes_of[int(row["montage_id"])])}
                           for row in montages.get(session, [])),
            groups=tuple({"group_id": int(row["group_id"]), "serial": str(row["probe_serial"]),
                          "insertion_number": int(row["insertion_number"]),
                          "electrode_config_hash": _str(row["electrode_config_hash"]),
                          "probe_type": _str(row["probe_type"]), "n_electrodes": _int(row["n_electrodes"]),
                          "montage_ids": tuple(sorted(members[(*session, int(row["group_id"]))])),
                          "sortable_runs": tuple(sorted(group_runs[(*session, int(row["group_id"]))]))}
                         for row in groups.get(session, [])),
            placed=tuple({"serial": str(row["probe_serial"]), "insertion_number": int(row["insertion_number"]),
                          "start_s": _float(row["start_s"]), "end_s": _float(row["end_s"])}
                         for row in placed.get(session, [])),
        )
    return stored


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def write(session_key: dict, stored: StoredMontages) -> None:
    """Replace the session's rows with `stored`'s, in one transaction. A montage a request's activation names
    cannot be deleted (its foreign key), so a session with one is refused here whole rather than rewritten under
    it: the stage never reaches this for such a session (plan decision D8)."""
    import datajoint as dj

    from wl_preproc.schema import core

    key = {name: session_key[name] for name in _SESSION}
    with dj.conn().transaction:
        # Children before parents: `delete_quick` does not cascade, so a row something else still names stops it.
        for table in (core.SharedGroup.SortableRun, core.SharedGroup.Member, core.SharedGroup,
                      core.MontageSortableRun, core.MontageProbe, core.PlacedPosition, core.MontageDerivation.Flag,
                      core.MontageDerivation, core.Montage):
            (table & key).delete_quick()

        core.MontageDerivation.insert1({**key, "positions_status": stored.status,
                                        "positions_seq": stored.positions_seq, "derived_at": _now()})
        core.MontageDerivation.Flag.insert([
            {**key, "flag_number": number, "code": flag["code"], "message": flag["message"],
             "run_number": flag["run_number"]} for number, flag in enumerate(stored.flags, start=1)])
        core.Montage.insert([{**key, "montage_id": m["montage_id"], "start_s": m["start_s"], "end_s": m["end_s"]}
                             for m in stored.montages])
        core.MontageProbe.insert([
            {**key, "montage_id": m["montage_id"], "probe_serial": p["serial"],
             "insertion_number": p["insertion_number"], "electrode_config_hash": p["electrode_config_hash"],
             "probe_type": p["probe_type"]} for m in stored.montages for p in m["probes"]])
        core.MontageSortableRun.insert([
            {**key, "montage_id": m["montage_id"], "probe_serial": p["serial"], "run_number": run}
            for m in stored.montages for p in m["probes"] for run in p["sortable_runs"]])
        core.PlacedPosition.insert([
            {**key, "insertion_number": p["insertion_number"], "probe_serial": p["serial"],
             "start_s": p["start_s"], "end_s": p["end_s"]} for p in stored.placed])
        core.SharedGroup.insert([
            {**key, "group_id": g["group_id"], "probe_serial": g["serial"], "insertion_number": g["insertion_number"],
             "electrode_config_hash": g["electrode_config_hash"], "probe_type": g["probe_type"],
             "n_electrodes": g["n_electrodes"]} for g in stored.groups])
        core.SharedGroup.Member.insert([{**key, "group_id": g["group_id"], "montage_id": montage_id}
                                        for g in stored.groups for montage_id in g["montage_ids"]])
        core.SharedGroup.SortableRun.insert([{**key, "group_id": g["group_id"], "run_number": run}
                                             for g in stored.groups for run in g["sortable_runs"]])

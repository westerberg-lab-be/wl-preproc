"""Shared groups: each set of montages an electrode was selected in at one position (spec 2 decision 3 and 4.4).
Pure: no database, no I/O.

Groups may overlap. For A -> B -> A at one position, A-and-B is shared over montages 1-3 and all of A over montages 1
and 3. A probe with no channel map read here (hash None) shares every channel across its members.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from wl_preproc.montage.derive import DerivedMontage


@dataclass(frozen=True)
class DerivedGroup:
    group_id: int
    serial: str
    insertion_number: int
    montage_ids: tuple[int, ...]
    electrodes: tuple[int, ...] | None
    member_hashes: tuple[str, ...]  # the members' hashes in montage order; () when the electrodes are None
    sortable_runs: tuple[int, ...]


def shared_groups(
    montages: Sequence[DerivedMontage], electrodes_of: Mapping[str, frozenset[int]],
) -> tuple[DerivedGroup, ...]:
    # (serial, insertion_number) -> its members: (montage_id, hash, sortable_runs), in montage order
    members: dict[tuple[str, int], list[tuple[int, str | None, tuple[int, ...]]]] = {}
    for m in sorted(montages, key=lambda m: m.montage_id):
        for p in m.probes:
            if p.sortable_runs:
                members.setdefault((p.serial, p.insertion_number), []).append(
                    (m.montage_id, p.electrode_config_hash, p.sortable_runs))

    found: list[tuple[str, int, tuple[int, ...], tuple[int, ...] | None, tuple[str, ...], tuple[int, ...]]] = []
    for (serial, insertion), ms in members.items():
        if len(ms) < 2:
            continue
        runs = lambda ids: tuple(sorted(r for mid, _, rs in ms if mid in ids for r in rs))  # noqa: E731
        if any(h is None for _, h, _ in ms):
            ids = tuple(mid for mid, _, _ in ms)
            found.append((serial, insertion, ids, None, (), runs(set(ids))))
            continue
        selected_in: dict[int, list[int]] = {}
        for mid, h, _ in ms:
            for e in electrodes_of[h]:
                selected_in.setdefault(e, []).append(mid)
        for ids in {tuple(v) for v in selected_in.values() if len(v) >= 2}:
            hashes = tuple(h for mid, h, _ in ms if mid in ids)
            common = frozenset.intersection(*(electrodes_of[h] for h in hashes))
            found.append((serial, insertion, ids, tuple(sorted(common)), hashes, runs(set(ids))))

    found.sort(key=lambda g: (g[1], g[0], min(g[2]), -len(g[2]), g[2]))
    return tuple(DerivedGroup(i, s, ins, ids, el, hs, rs) for i, (s, ins, ids, el, hs, rs) in enumerate(found, 1))

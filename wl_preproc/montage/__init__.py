"""Montages: the probe configurations a session passes through, derived once from the probe positions wl.works sends.

Four modules, one definition of each thing:

- `edges`: places one position edge (a wall time) in a gap between runs, pure.
- the derivation of montages and their boundaries from the placed edges.
- the groups of runs that share electrodes within a montage.
- the stored rows both are written to.

One definition: the listing, the request checks and the NWB build read montages and groups from the stored rows and
never re-derive them.
"""

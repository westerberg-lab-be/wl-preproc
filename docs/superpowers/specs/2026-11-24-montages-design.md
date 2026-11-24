# Montages recognized here, and the electrodes they share — design

**Date:** 2026-11-24. **Status:** draft, for the requester's approval.
**Asked by:** the requester, 2026-11-24 (the moved-probe join, placed next after the wl.works-facing piece).
**Supersedes:** in part, `2026-08-22-phase-2a-ephys-schema-design.md` §3.2.2 ("the overlap case is a derivative",
requested by hand): an overlap is now sorted always.

## 1. Intent

A probe advanced mid-session joins no insertion today, so every file lists it with area `unknown`
(`ephys.link_insertions`, `schema/ephys.py:654-740`, and the same rule again in `nwb/gather.py::_probes`, :879-889:
a serial reported for two insertions is joined to neither). The cause is that nothing here knows *when* each insertion
held: requests carry insertions without times, and the latest request overwrites an insertion by number
(`responder/jobs.py::_record_probe_reports`).

The requester's requirement, verbatim (2026-11-24):

> it is important to remember that with probes like neuropixels, it is not just when the probe moves, but also which
> channels are selected. so we should be careful to recognize montages. and when there is partial montage overlap
> (e.g., half the channels are kept at the same depth, but the other half are changed between runs)

So a montage is a probe's **position and its channel selection**, and two montages can share part of a selection.
The why, as far as the requester has said it: a neuron recorded on an electrode that stayed in place should be one
unit, however the rest of the probe's selection changed around it, and each file's probe should carry the right
insertion and area.

## 2. What the requester decided (2026-11-24, in the multiple-choice UI)

1. **Shared electrodes are sorted across, always.** Besides each montage's own sort, the electrodes two or more
   montages share are sorted together across those montages, so a unit on them carries one identity throughout.
2. **"Shared" means the same electrode selected while the probe did not move.** A move ends sharing: no depth
   arithmetic, no pairing of electrodes across a move.
3. **Identical selections at one position group together; each electrode is sorted across the whole stretch it was
   recorded.** For A → B → A at one position: one shared sort on A ∩ B over all three montages, and one on all of A over
   both A's. B's own electrodes have only B's montage sort.
4. **wl-preproc recognizes everything.** wl.works sends each probe's position times; this host derives the montages
   and the shared groups from them and from each file's channel selection, and lists both. wl.works fires what is
   listed and stops deriving montages (it offered to, and recorded this in its Plan 20 §15, wl-works `e64ecc30`).

## 3. Goals, non-goals, anti-goals

**Goals.**
- One definition of a montage, here, checked against the files.
- Each SpikeGLX file's probe joins the insertion whose position covers the file's time; the area follows.
- Every shared group is recognized, listed, and requestable, with exactly its electrodes, montages and runs.

**Non-goals.**
- Running any sort. Sorting is Phase 2b and waits on the compute machine; this piece stores what each sort will cover.
- Depth, trajectories, or per-electrode anatomy (still the insertion's area, as today).
- Tracking units across a move, or across sessions.

**Anti-goals** (would make this a failure even if it works):
- Two electrodes treated as shared when the probe moved between them.
- A montage boundary guessed where the evidence does not place it: an ambiguous edge stays ambiguous and is flagged.
- A montage renumbered after something names it.
- A sort fired without a person's act (wl.works' rule: an agent never triggers a send).

## 4. The design

### 4.1 Positions: what wl.works sends

A new request, **`PUT /positions`**, carries the *whole* set of one session's probe positions, every time (the shape
`PUT /nwb/active` already uses):

```
{ "subject": ..., "session_datetime": ..., "positions_seq": <int, >= 0>,
  "positions": [ { "serial": "...", "insertion_number": 1..255,
                   "inserted_at": <wall time>, "ended_at": <wall time> | null,
                   "end_kind": "withdrawn" | "moved" | null,
                   "inserted_after_run": <run_number> | null,
                   "ended_after_run": <run_number> | null } ] }
```

- `insertion_number` stays wl.works' (1-255, stored once, never renumbered: its P12). `ephys.InsertionReport` stays
  keyed on it; a job request's `probes` still carries each insertion's serial, trajectory, target and assignment.
- Times are naive UTC wall times, as every datetime this host takes, at a person's precision: a time on a whole
  minute `m` stands for `[m, m + 60 s)`, as wl.works' `deriveMontages` reads it; one with seconds stands for that
  second. This host places them on session time through `timebase.ClockReference`'s t = 0 (stored by the
  wl.works-facing piece).
- `*_after_run` is the gap a person named when the time alone is ambiguous (§4.2); it overrides the time.
- **The highest `positions_seq` wins, until the session's first accepted job request** (wl.works' objection O1,
  2026-11-24: two quick edits can put two requests in flight, and arrival order is not edit order). `positions_seq` is
  wl.works' own revision counter for that session's positions. A request whose seq is below the stored one is a `409`
  carrying the stored seq; an equal seq with the same set is a no-op `200`, and with a different set a `409`. After
  the first accepted job request, a changed set is a `409` naming that request, and the same set again is a no-op.
  (wl.works' question 2.) That `409` holds for the session's life in this piece: a replacement does not reopen
  positions. Reopening them under a replacement is later work, since it re-derives montages a stored activation names.
- **No author is carried** (objection O3): positions are written by many people and agents, and a person's name
  leaves wl.works only in a job request.
- **A session this host has not landed** is a `404`, as `GET` of an unknown session is; wl.works sends positions only
  once the landed session is linked to its visit, and sends the whole set then.
- **Gaps in `insertion_number` are fine** (1, 3, 4): it is wl.works' identity for a position, not an ordinal here.
  Until the first accepted request nothing is fixed, so a number that leaves and comes back is whatever the latest set
  says.
- wl.works sends positions **as soon as they are typed and on every edit**, not at Confirm, so the listing carries the
  montages before a person confirms and the person confirms what they see. (wl.works' question 1.)
- **A session with no ephys recording** (no SpikeGLX and no Intan files) needs no positions: it has one montage over
  all its runs, at once, and behaviour-only sessions are unchanged. **Every other session waits for a
  `PUT /positions`**, even one whose census finds no probe, because an Intan probe is never in the census
  (`ProbeCensus` reads SpikeGLX only) and could have moved. An empty `positions` list is a valid answer.

### 4.2 Placing an edge

Each position's start and end is an **edge**, placed in a gap between runs (the logic moves here from wl.works'
`deriveMontages`, kept as it is there; wl.works' question 3). An edge's **window** is the span its time stands for
(§4.1), widened by 60 s each side only when t = 0 came from the manifest (`t0_source == "manifest"`, up to 60 s off);
with t = 0 from the barcodes, the rig's clock or the NI's metadata it is not widened. Until t = 0 is stored, no edge
is placed (`positions_status` stays `waiting`).
- **Placed:** the window touches exactly one gap and no run. Or a person named the gap with `*_after_run`.
- **Ambiguous:** the window touches more than one gap. Flag `position_edge_ambiguous` (run numbers of the candidate
  gaps in the message); **no montage is listed for the session** until a person names the gap.
- **Inside a run:** the window touches one run and no gap, or one run and one gap's edge only by overlap with
  the run. The edge is placed in the gap *after* that run, the run is left out
  of that probe's sortable runs (§4.4), and flag `position_edge_inside_run` names the run and the probe. Nothing is
  guessed: no part of that run is used for that probe either side of the move.
- A probe's first insertion and its last withdrawal split nothing (wl.works' P11).

### 4.3 Montages

A **montage boundary** falls at a gap where any probe **changes position** (a `moved` end and the next position of
that serial, or a re-insertion after a withdrawal), or where any probe's **channel selection changes** between runs
(`ProbeCensus.Probe.electrode_config_hash`, per SpikeGLX file). Montages are session-wide, so within one montage each
probe has at most one position and one selection.

- **A selection change inside a run** (a SpikeGLX restart mid-run) stays the existing flag `bank_change_in_run`, now
  this host's alone (wl.works' question 6). The boundary falls after that run, and the run is left out of that probe's
  sortable runs.
- **The canonical file of a montage that holds such a run is built**, no longer refused: `nwb/gather.py::_probes`
  stops refusing a probe with two site maps under the file's runs, and gives the probe the selection its sortable
  runs in that montage share, with a note naming the run where it changed. A probe with no sortable run in the
  montage is listed with no electrodes and that note. *My call*: a file refused for ever helps nobody, and the sorts
  never see the run.
- **A move inside one SpikeGLX file** (no restart) is fine: each montage takes the file's time inside its own window.
  A move inside a run leaves the run in the earlier montage, so that file carries the probe's earlier insertion and
  area, and the run is not sortable for the probe (§4.2).
- **Numbering:** montages are numbered 1, 2, … in time order when first derived, and stored in `core.Montage`. Until
  the first accepted request they are re-derived freely; after it they are fixed (positions are then fixed too, §4.1).
- A montage's window is `[its first run's start, the next montage's first run's start)`; the last ends at its last
  run's end (wl.works' P10, kept).

### 4.4 Shared groups

For each probe at one position (one insertion), and each electrode it recorded there, take **the set of that
position's montages in which the electrode was selected**. Each distinct set `S` that some electrode has, with two or
more montages, is a **shared group**: it sorts **every electrode selected in all of `S`'s montages**, over `S`'s runs.
A set of one montage is that montage's own sort.

So groups may overlap, as the requester chose (§2 decision 3). For A → B → A the sets are {1, 2, 3} (the electrodes
of A ∩ B), {1, 3} (those of A only) and {2}; the shared groups are A ∩ B over montages 1-3, and **all of A** over
montages 1 and 3, so A ∩ B's electrodes are in both. Each group is its own sort with its own units; an electrode's
longest-stretch identity is the group with the most montages. Overlap is also what a sorter needs: a neuron's
waveform spans neighbouring electrodes, and a group cut to disjoint electrodes would cut that footprint in two.

- A probe whose selection never changed while another probe's change split the session gets one shared group over
  its whole position: every electrode, every montage. This is the requester's rule applied, not a special case.
- Each montage, and each shared group, lists **per probe the runs it can be sorted on**: the runs it covers, less
  those §4.2 and §4.3 left out for that probe. Bad runs stay wl.works' to mark: a request's `probe_runs` must be a
  subset of the listed sortable runs (a `422` naming the run otherwise), and is otherwise as today (question 5).
- Groups are numbered per session (`group_id` 1, 2, …), fixed like the montages, and stored: a new
  `core.SharedGroup` (subject, session_datetime, group_id; serial, insertion_number) with parts naming its montages and
  its electrodes, the electrodes as an `ephys.ElectrodeConfig` minted by the existing `intersect_electrode_configs`.
- **An Intan probe** (never in the census) takes part through its positions only: its moves bound montages, and at
  one position its channels never change, so a probe at one position over several montages gets one shared group,
  listed with `electrodes` null, meaning every channel it recorded (*my call*: this host reads no Intan channel map).
- No floor on a group's size: a group of two electrodes is listed like one of three hundred (*my call*: whether a
  tiny group is worth a sort is the sorter's parameter, decided with Phase 2b).

### 4.5 The listing

`SessionEntry` gains, null until the montages can be derived (positions stored and every edge placed, or no probe):
- `montages: list[ListedMontage] | None`: `montage_id`, `start_s`, `end_s`, `run_numbers`, and per probe `serial`,
  `insertion_number`, `electrode_config_hash`, `sortable_runs`.
- `shared_groups: list[ListedSharedGroup] | None`: `group_id`, `serial`, `insertion_number`, `montage_ids`,
  `n_electrodes`, `electrodes` (null for an Intan probe, §4.4), `sortable_runs`.
- `positions_seq: int | None`: the seq the listed montages and groups were derived from (objection O2). wl.works
  refuses *Confirm* while it is behind its own, and fires only requests naming montages and groups listed at its
  current seq, so a person cannot confirm one set and fire another. Null when no positions are stored.
- `positions_status`: `"not_needed" | "waiting" | "placed" | "ambiguous"`, so wl.works can say why montages are null
  (`waiting` covers both "no positions yet" and "no t = 0 yet").
- New flag codes: `position_edge_ambiguous`, `position_edge_inside_run`, `probe_without_position` (a recorded serial
  that no position names: its runs are sortable for no montage, and its area stays `unknown` with that reason).

### 4.6 Requests

- **A canonical names one listed montage** (`selection.montage_id`) as today. `metadata.montage_boundaries` is
  retired the way `metadata.blocks` was: kept in the shape, `maxItems: 0`, deprecated, so a sender is told what
  replaced it.
- **A shared group's request is a new role, `shared`**, naming `selection.group_id` and its `probe_runs` for that one
  probe. Its `metadata.probes` is exactly that one probe's entry (one `InsertionReport` row, written as today); its
  idempotency key and its answers are a canonical's (wl.works' question Q4). It is neither a canonical (it spans montages) nor a hand-picked derivative (its electrodes and montages are
  fixed by the listing). wl.works fires it **on the same person's act that fires the canonicals** — Confirm — one per
  listed group (wl.works' question 4; "always" means every confirmed session, never an agent).
- Until sorting exists, a `shared` activation is accepted and stored, and its build waits ("waiting on sorting").
  The schema change the role needs (an activation keyed by a group rather than a montage, or a sibling of
  `request.Activation`) is left to the builder, under §5's constraints.

### 4.7 Joining files and insertions

- `ephys.link_insertions` joins each (SpikeGLX file, serial) to every insertion whose placed position overlaps the
  file's time, so a file spanning a move links to both. The "a serial reported twice links neither" rule goes.
- `nwb/gather.py::_probes` takes each serial's insertion from the montage the file is built for (exactly one by
  construction), so its area follows. Its own copy of the old rule goes with it.
- A canonical file's description names the shared groups that touch its montage (group id, montages, number of
  electrodes), so a reader can find the shared file. *My call*: the canonical file is otherwise unchanged, and stays
  self-contained.
- A shared group's NWB file (Phase 2b, built from its sort) carries the group's electrodes only, the intersection,
  answering the phase-2a spec's open question 2.

## 5. Constraints

- `insertion_number` is wl.works', 1-255, never renumbered; `montage_id` and `group_id` are this host's, never
  renumbered once a request names them.
- One definition: `listing/`, the request checks and the NWB build read montages and groups from the stored rows,
  never re-derive them.
- A change that alters what a stored activation covers is refused, never applied quietly.
- wl.works pins once, at this piece's merge: `job_request.json`, `session_listing.json`, and the new positions
  schema. Development databases are rebuilt, never migrated.

## 6. Approaches

1. **wl-preproc recognizes everything (chosen).** One definition, checked against the files; wl.works deletes
   `deriveMontages` and fires what the listing names. Costs a new message and a change to wl.works' flow.
2. **Split.** wl.works keeps placing moves and tags each insertion with its montage; this host adds the
   selection splits and the groups. Rejected: wl.works' bank-change rule would stay a second copy of ours.
3. **wl.works recognizes everything** from the per-file electrode lists already listed. Rejected: electrode logic
   would live away from the files it describes.

## 7. Left to the builder

- The exact positions schema file and model names, the `PUT /positions` status codes beyond those above, and where
  the stored positions live (a `request`-side table is the natural home).
- How a `shared` activation is keyed (§4.6), so long as `Clustering` resolves one effective `ElectrodeConfig` and
  its key names what unit identity holds over.
- The order montages and groups are derived in, and the daemon stage that does it.

## 8. wl.works' review (2026-11-24)

Three objections, adopted: O1 `positions_seq` orders the requests (§4.1), O2 the listing echoes it (§4.5), O3 no
author in positions (§4.1). Four questions, answered in place: Q1 a session not landed is a `404`, Q2 gaps are fine,
Q3 the `409` after firing holds for the session's life in this piece, Q4 a `shared` request carries its one probe and
keys and answers like a canonical. wl.works ships 20b-1b against today's contract, and adopts this piece in a plan of
its own.

## 9. Open, for the requester

- None. The builder check (2026-11-24) asked four questions: overlapping groups (answered by §2 decision 3, and §4.4
  now says so), the edge window (kept as wl.works has it, §4.2), the canonical file holding a mid-run selection change
  (built, §4.3, my call) and Intan probes (they wait for positions and bound montages, §4.1 and §4.4, my call).

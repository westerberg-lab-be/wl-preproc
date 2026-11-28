# The console-settings reader: a form is an act, a withdrawal is a row, and each look's size and chromaticity are published

wl-xcon's console settings and withdrawn changes (XC-513, XC-529) are read. The change log knows the kinds `form`,
`shaping` and `withdrawn`, a form counting as one act and a withdrawal's stager published by kind only; each trial
line's training switch, and each look's size, object window and drawn chromaticity (`light_xy`), reach the NWB file;
the synthetic rig writes the new shapes. A look's light in cd/m² is not read: that is a later piece.
- **Spec:** `docs/superpowers/specs/2026-11-28-console-settings-reader-design.md` (`b5b3cce`).
- **Plan:** `docs/superpowers/plans/2026-11-28-console-settings-reader.md` (`6ed4241`), six tasks, decisions D1 to D7.
- **Branch:** `feat/console-settings`, from the plan.
- **The ledger:** `.superpowers/sdd/2026-11-28-console-settings-reader/progress.md` holds the full wording of every
  ruling below.

## What was built

By task, each with its commits.
- **Task 1, the change log's three new kinds** (`3ac0a70`). `form`, `shaping` and `withdrawn` in `KINDS`; a form
  joins the acts; `staged_by_kind` publishes a withdrawal's stager by kind only; the two acts docstrings re-exported.
- **Task 2, the trial line** (`05bf4a9`). Each line's training switch (`shaping`), and each look's size, object window
  and drawn chromaticity, read as `AsDrawn`; an unreadable one is none, said once per field.
- **Task 3, the stimulus tables** (`79fb4ed`, `02223cc`). `size_deg`, `window_deg` and `light_xy` in `stimuli` and
  `stimulus_objects`, only where the record carries them; only those columns are optional.
- **Task 4, the texts** (`225694e`). A block may end by `shaping`, a size may vary by `scaled`, and the colour texts
  and `window_deg` are corrected.
- **Task 5, the synthetic rig** (`7c05a89`, `4e9c317`). `RigRecordSpec.console` writes wl-xcon's shapes, and a built
  file shows each; the stager's name and account are absent from the whole file.
- **The final fix wave.** Any mapping carrying a `name` or an `account` is published by kind only in every change and
  control `detail`, known kind or not; the `ended` text follows wl-xcon's addendum; the spec carries an amendment
  note; the records above say what files built earlier hold.
- **Task 6, the records.** `docs/pending-wl-works-amendments.md` (one block), `HANDOVER-wl-expcontroller.md` (one
  section), this handoff, and the correction of one sentence in `docs/CHECKPOINT.md`.

## Rulings and choices

In the order made, each with what it costs if wrong. Copied from the ledger.
- Task 3: Ruling (re-grade by effect): add_rig_tables' `if field in rows[0]` filter now applies to EVERY table column, so a later typo or missing key silently drops a published column where it raised KeyError before; make only the console columns (size_deg, window_deg, light_xy) optional, every other column still required — plus a per-eye test with two objects of different AsDrawn per eye pinning each row by (eye, object) — cost: one small round.
- Task 4: Ruling: 225694e (and any implementer commit) whose Co-Authored-By names Claude Sonnet 5.5 is left as is, as in the last two pieces — it names the model that wrote it — cost if wrong: inconsistent trailers.

Decisions D1 to D7 of the plan bind: the kinds (D1), what the new rows publish (D2), the stimulus tables' columns (D3),
a trial's `shaping` (D4), the synthetic rig (D5), no table and no contract field changes (D6), and `object_window`
(D7).

### The requester's choices

- **2026-11-28, execution:** subagent-driven.
- **2026-11-28, the light:** the requester accepted wl-xcon's shape, `light_xyz_cd_m2`: CIE 1931 XYZ in cd/m²,
  written beside each look's `light_xy`, null wherever `light_xy` is null. wl-xcon has not built it and will tell us
  the commit. This piece publishes `light_xy` only and computes no light. The earlier ask for
  `light_luminance_cd_m2` and `light_colorset` was dropped in favour of this.

## The task reviews

Tasks 1 to 6 each had a review (Sonnet), all with the spec confirmed. Counts are failures of review, not of tests.
- **Task 1:** approved, 4 Minor. **Task 2:** approved, 4 Minor.
- **Task 3:** approved, 4 Minor, after one fix round (`02223cc`): the filter on missing columns had reached every
  column, and the per-eye test did not pin each eye.
- **Task 4:** approved, 3 Minor (one routed to Task 6, the CHECKPOINT sentence on a look's light).
- **Task 6:** approved (Sonnet), spec confirmed, 4 Minor; every claim made to wl.works or wl-xcon was checked
  against the code at HEAD.
- **Task 5:** one Important (the check that the stager's name is absent filtered on `dtype == object`, which a
  pandas 3 text column is not, so it was likely vacuous) and 4 Minor; the Important was fixed in `4e9c317` with a whole-file byte scan and a proof
  that it fails when a name leaks.

### For the whole-branch review

Named checks, from the plan's Review Focus:
1. **A withdrawal names who staged it.** `staged_by` has a name and an account. Neither may reach the NWB file. Its
   kind may. (Tasks 1 and 5.) The database keeps the line whole, `ParameterChange.row`, as every actor's already
   is; it is read only through `change_fields` (which leaves `staged_by` out) and the act counts.
2. **A record from before the console build.** A session with no `size_deg`, `window_deg` or `light_xy` anywhere
   builds stimulus tables with exactly today's columns. (Task 3.)
3. **A `light_xy` or size wl-xcon wrote unreadably** (a single number, three numbers, text) is none, said once per
   field, and the trial line is kept. (Task 2.)
4. **A revert that takes the training switch back** is a `shaping` row with a `moment`, not a `revert`, counted as
   no act. (Task 1.)
5. **A per-eye array.** Its objects' sizes and `light_xy` sit under `left` and `right`, read and published in the
   same rows as the objects' positions. (Tasks 2 and 3.)

### The whole-branch review

`FINAL_REVIEW`

## Deferred minors

The ledger holds the full wording of each. Copied from it.
- Task 1: minor (deferred): test_writers' "Ada" not in repr(...) assertion is vacuous (the writer gets extracted fields); staged_by_kind description promises box/member/system without a vocabulary check; two over-wide docstring/description lines.
- Task 2: minor (deferred): _places signature/test line >120 chars; three-tuple return indexed [0]/[1]/[2] (a named tuple would read better); quote style; awkward drawn-description line break.
- Task 3: minor (deferred): description assertions cover two of the five new descriptions.
- Task 3: minor (deferred): OPTIONAL_COLUMNS derived from rigtrials.CONSOLE_KEYS (coupling); the fix round's tests/nwb reference run not repeated (the merge suite runs it).
- Task 4: minor (deferred): BlockEnd docstring's `ended` text is not the brief's verbatim parenthesised form (same meaning); several long description lines.
- Task 5: minor (deferred): _start_fields reads rig_record.console unguarded (consistent with _end_fields); lint not run (ruff not in venv).
- Task 5: minor (deferred): the byte scan relies on vlen text sitting in HDF5's global heap outside chunk compression (true today; say so in a comment).

## Carried to later parts

- **A look's light in cd/m².** Once wl-xcon writes `light_xyz_cd_m2` and tells us the commit, a small piece reads
  it. Task 5's fixture already carries a realization's `macaque_row`.
- **The XC-543 pop-out revision reader** (`trial_kind`, `stepped`, `found_by`, `odd_place`, the objects' `drawn`).

## The suite

`SUITE_RESULTS`

## Deploy

- **Development databases are rebuilt, never migrated.** No table changes: `core.ParameterChange` keeps each line
  whole, and `SCHEMA_VERSION` stays 3.
- **Two descriptions change** (`nwb_description.json`, `session_listing.json`; no field added or removed), so every
  listed session's description reads differently once.
- **The listed act count rises by one per form row from deploy;** a file built earlier keeps the count it was built
  with until it is rebuilt.
- **Files built before this merge may carry a stager's name.** Before it, a `withdrawn` row was a kind this reader
  did not know, and its `detail` was published as written, `staged_by`'s name and account included. Any NWB file built
  before this merge from a session with a withdrawn row must be found (the `core.ParameterChange` rows whose stored
  `row` has `"kind": "withdrawn"`) and rebuilt. No lab host has run this pipeline, so no real file is known to carry
  a name today; the step is for the first host that does.
- **`wl.yaml`:** no dependency changed.

## Told to

Nobody yet. At the merge:
- **wl.works:** the block "Added by the console-settings reader (2026-11-28)" in
  `docs/pending-wl-works-amendments.md` lists every change. Two schema files change, descriptions only, so a pin is
  optional.
- **wl-xcon:** the section "2026-11-28: the console settings and withdrawn changes (XC-513, XC-529)" in
  `HANDOVER-wl-expcontroller.md`, which also says `light_xyz_cd_m2` is accepted and waits for their commit.

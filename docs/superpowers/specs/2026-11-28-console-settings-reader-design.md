# Reading wl-xcon's console settings (XC-513) and withdrawn changes (XC-529) — short design

**Date:** 2026-11-28. **Status:** approved by the requester 2026-11-28 (the multiple-choice question).
**Sources (wl-xcon main `293a66e1`):** `docs/pending-wl-preproc-amendments.md`, the sections "ANSWERED 2026-11-18 —
the console settings" (XC-513, its 2026-11-18 addendum from `d67cfa08`, and `light_xy`) and XC-529 ("withdrawn
rows"); `wl_xcon/record.py`. The requester accepted every shape there on 2026-11-18 and 2026-11-19. This design
decides only how this host reads them.

## Intent

wl-xcon has written these records since 2026-11-18. Today this host passes them through unread: the new change kinds
are published as written and flagged "unknown kind" (the agreed holding behaviour), and sizes, drawn colours and the
training switch are not read at all. Two published texts are now false: the colour descriptions
(`nwb/run_record.py:38`, `nwb/trial_record.py:49`) say "a look's light is its colour's xyz times its fraction", true
only at saturation 1; and `object_window` in a trial's params is no longer in degrees (it is a multiple of the
object's radius). This piece reads every new shape, so the database and NWB files carry them, and corrects both texts.

## What is read, and where it lands

1. **Change rows** (`xcon/parameter_changes.jsonl`, `events/rigrecords.py`): `form`, `shaping` and `withdrawn` join
   `KINDS`, so they are no longer "unknown".
   - `form` (a person sets a distribution or a scaling; it strobes `PARAM_CHANGED`) joins `SETTING_KINDS`: it is a
     person's act. Its `now` and `detail.shape` are kept.
   - `shaping` (the training switch, `was`/`now` in on|frozen|full, not strobed) is a control-like change, timed by
     `at` through the rig clock fit as `training` and `drum` rows are. Not a setting act.
   - `withdrawn` is published as the withdrawal of a change that never took effect (it had no row of its own). Its
     `cid`, `withdrawn.kind`, `withdrawn.now` and `staged_by` are kept. It is not counted as an act, and nothing is
     undone (nothing was applied).
   - Timing is unchanged: rows are joined to strobes by their `event_code`, never by kind.
2. **Trials** (`xcon/trials.jsonl`, `events/rigtrials.py`): each trial gains `shaping` (on|frozen|full, null in
   records before the build), published as a trials column. `drawn[name]` accepts the token `"full"`.
3. **Stimuli and objects** (each `stimuli[]` and `objects[]` entry): `size_deg`, `window_deg` (objects only) and
   `light_xy`, published as columns of the NWB `stimuli` and `stimulus_objects` tables, null where wl-xcon writes
   null and in records before the build.
4. **The light, computed** (a new small pure module): for a look with `light_xy` = (x, y), wl-xcon's formula,
   verbatim: `u = (x/y, 1, (1 − x − y)/y)`; `L = fraction × luminance`; `XYZ = u × L / (macaque_row · u)`, in
   cd/m². `fraction` and `luminance` are the look's `ColorSetLight` values from the trial's params; `macaque_row` is
   the realization entry of the look's colour set at that luminance (`color_realized` or the run's `color_sets`).
   Published as `light_xyz_cd_m2` beside `light_xy`; null where any input is missing.
5. **Runs** (`events/rigruns.py`): a block end's `ended` may be `"shaping"`, and `varied[name].by` may be
   `"scaled"` (with `from` and `factor`). Both are already read as text; their descriptions name them. `color_sets`'
   `design` and `macaque_row` stay inside the published JSON text.
6. **Descriptions corrected**: the two colour texts say the light is computed from `light_xy` (column
   `light_xyz_cd_m2`), not from xyz × fraction; the params' `object_window` is a multiple and `window_deg` is the
   degrees; every place listing a block end's `ended` values (`events/rigruns.py`, `nwb/run_record.py:63`,
   `nwb/rig_record.py`'s `unopened_blocks`) gains `shaping`.

## Constraints

- Absent keys read as before, unreadable ones as none, reported once per field (`events/rigfields.py`), so records
  written before the build still load.
- Floats through the light formula are compared to a tolerance in tests.
- Our synthetic xcon writer (`synth/peripherals.py`) writes the new keys, since wl-xcon has no checked-in fixture.
- Published descriptions change, so `contracts/nwb_description.py`'s version and wl.works' notice follow the
  existing pattern.

## Approaches

One sensible way: the shapes are fixed by an ask the requester accepted, and each lands in the reader that already
owns its file. Computing the light on wl.works' side instead was rejected: the formula needs the calibration's
`macaque_row`, which only this host reads.

## Left to the builder

Column names beyond those above, the module name for the light formula, and how a look's luminance is matched to its
realization entry (by its `luminance_cd_m2`, as wl-xcon realizes them).

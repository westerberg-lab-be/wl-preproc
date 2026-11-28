# Reading wl-xcon's console settings (XC-513) and withdrawn changes (XC-529) — short design

> **Amended 2026-11-28 (after the build).** Two things below no longer read as built. (1) The requester accepted
> wl-xcon's `light_xyz_cd_m2` (CIE 1931 XYZ in cd/m², written beside each look's `light_xy`, null with it, not yet
> built upstream) in place of item 4's luminance and colour-set ask; reading it is a later piece. (2) Item 3's
> "null in records before the build" is met by decision D3: such files get no new columns at all, so absence reads
> as null to any reader.

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
4. **The light is not computed in this piece** (the requester's choice, 2026-11-28, after the plan found the
   spec's premise false). A trial line gives each look's `light_xy` but not which `ColorSetLight` drew it, and the
   form is often declared in the task, not the trial's params, so `fraction × luminance` cannot always be found; a
   rule that guesses could borrow another look's luminance unnoticed. This piece publishes `light_xy` only. **wl-xcon
   is asked** to write, beside each look's `light_xy`, its light's macaque luminance (cd/m², `fraction × luminance`)
   and its colour set's name, so a later small piece computes `XYZ = u × L / (macaque_row · u)` exactly, with
   `u = (x/y, 1, (1 − x − y)/y)` (wl-xcon's formula, verbatim).
5. **Runs** (`events/rigruns.py`): a block end's `ended` may be `"shaping"`, and `varied[name].by` may be
   `"scaled"` (with `from` and `factor`). Both are already read as text; their descriptions name them. `color_sets`'
   `design` and `macaque_row` stay inside the published JSON text.
6. **Descriptions corrected**: the two colour texts say that `xyz × fraction` is a look's light only at saturation
   1, and that a look's drawn chromaticity is its `light_xy`; the params' `object_window` is a multiple and `window_deg` is the
   degrees; every place listing a block end's `ended` values (`events/rigruns.py`, `nwb/run_record.py:63`,
   `nwb/rig_record.py`'s `unopened_blocks`) gains `shaping`.

## Constraints

- Absent keys read as before, unreadable ones as none, reported once per field (`events/rigfields.py`), so records
  written before the build still load.
- Our synthetic xcon writer (`synth/peripherals.py`) writes the new keys, since wl-xcon has no checked-in fixture.
- Published descriptions change, so `contracts/nwb_description.py`'s version and wl.works' notice follow the
  existing pattern.

## Approaches

One sensible way to read the shapes: they are fixed by an ask the requester accepted, and each lands in the reader
that already owns its file. For the light, three were weighed on 2026-11-28: compute it only where the params name
one light (rejected: a rare case borrows another look's luminance silently), leave it to analysts (rejected: the
calibration's `macaque_row` is read here), and ask wl-xcon for the two missing values and compute it later (chosen).

## Left to the builder

Column names beyond those above. A withdrawal's `staged_by` and every other actor are published by kind only, never
by name (the plan's finding).

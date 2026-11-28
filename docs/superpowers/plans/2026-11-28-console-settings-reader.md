# wl-xcon's console settings (XC-513) and withdrawn changes (XC-529), read here: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** This host reads every shape wl-xcon has written since 2026-11-18 and stops publishing them unread. That
means the change kinds `form`, `shaping` and `withdrawn`, each trial's `shaping`, and each look's size, object window
and drawn chromaticity (`light_xy`). It also corrects the texts that are now false. The light in cd/m² is not
computed here (spec item 4): wl-xcon is asked for the two values it needs, and a later piece computes it.

**Architecture:** Each shape lands in the reader that already owns its file: `events/rigrecords.py` (change rows),
`events/rigtrials.py` (trial lines) and `events/rigruns.py` (run rows). The NWB build publishes them through the
tables that already exist. Nothing is stored in the database: change rows are already stored whole, and the stimulus
tables are built from the session folder at NWB build (`nwb/gather.py:1051`).

**Tech Stack:** Python 3.11/3.13, pydantic 2, DataJoint 2 on MySQL (testcontainer), pynwb/hdmf.

**Spec:** `docs/superpowers/specs/2026-11-28-console-settings-reader-design.md` at `b5b3cce` (approved
2026-11-28; amended the same day by the requester's ruling on the light). The spec is binding. The upstream shapes
are binding for keys and values: wl-xcon `main` at `293a66e1`, `docs/pending-wl-preproc-amendments.md` ("ANSWERED
2026-11-18 — the console settings" with its addendum, and the XC-529 section), and `wl_xcon/record.py` (`KINDS`,
`STROBED`, `_light_xy`, `_stimuli`). Read them only through `git -C ~/GitHub/wl-xcon show main:<path>`, and never
touch that working tree. Where this plan decides something the spec leaves open, the decision is named `D1`-`D7`
below and cited where it is used.

## Global Constraints

From the spec:
- Absent keys read as before, unreadable ones as none, reported once per field (`events/rigfields.py`), so records
  written before the build still load.
- Our synthetic xcon writer (`synth/peripherals.py`) writes the new keys, since wl-xcon has no checked-in fixture
  with them. Tests generate them here.
- Published descriptions change, so `contracts/nwb_description.py` and wl.works' notice follow the existing pattern
  (D6).
- A withdrawal's `staged_by`, and every other actor, is published by kind only, never by name (D2).
- Timing is unchanged: rows are joined to strobes by their `event_code`, never by kind.

For the build:
- Interpreters: 3.11 is `.venv/bin/python`; 3.13 is `~/.cache/wl-preproc-venv313/bin/python`. Run them one after the
  other, never in parallel (they share the test database). Run targeted tests during each task, and both interpreters
  on the touched test files at each task's end.
- pytest is run with `-o faulthandler_timeout=1800 -p no:cacheprovider`. Below, `PYTEST` stands for
  `.venv/bin/python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800`, and "both interpreters" means
  the same command again with `~/.cache/wl-preproc-venv313/bin/python`, after the first finishes.
- `REFERENCE_SET` stands for `tests/schema/test_nwb_*.py tests/schema/test_eye_by_copy.py
  tests/schema/test_intan_by_strobes.py tests/schema/test_ni_clock.py tests/schema/test_partial_sync_box.py`. Run it
  with every `WLPP_*` reference variable set, as at merge (the montages handoff's suite section names them).
- Schema export: `.venv/bin/wlpp schemas export --out docs/schemas`. Regenerate in the task that changes a contract
  model, and commit only the JSON that changed.
- Compare floats that pass through the light formula (or trig) to a tolerance, never as text.
- Stage files by name (never `git add -A`; a stray `wl-preproc` symlink at the repo root stays out). The commit
  trailer is:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01HzkMmyxfPZxnrtSqNEictD`.
- Refusals in pydantic validators raise PydanticCustomError, never ValueError.
- A fixture that contradicts the design is corrected, never answered with a code fallback.
- When a task changes what a shared reader or fixture helper produces, it runs every test file that reaches it,
  including indirect ones through helpers and `REFERENCE_SET`. Each task's last run step names them.
- The database-backed tests need Docker (OrbStack). Never commit while a test run is going on this checkout.

## Decisions on what the spec leaves to the builder or does not say

- **D1. The kinds.** `KINDS` gains `form`, `shaping` and `withdrawn`, in wl-xcon's order. `SETTING_KINDS` gains
  `form` alone, so a form is one of a person's acts (`acts`). `shaping` and `withdrawn` are known and counted
  nowhere. Every text that says which kinds count as acts names a form too: the two contract docstrings
  (`TrainingRecords`, `ListedTrainingRecords`) and the `parameter_changes` table.
- **D2. What the new rows publish.** No new `parameter_changes` column except **`staged_by_kind`**, a withdrawal's
  `staged_by` by kind only, read as `by` is read. `staged_by` joins `_CHANGE_FIELDS`, so it never reaches `detail`:
  the file names no one who acted (design spec 2026-10-18 §0, decision 6). The database keeps the whole line, as
  today. A form's `shape`, and a withdrawal's `cid` and `withdrawn` (`{kind, now}`), are row-own fields, so they
  stay in `detail` as written. A revert's `shaping` row keeps its `moment` in the existing `moment` column.
- **D3. The stimulus tables' new columns.**
  - `stimuli` gains `size_deg` and `light_xy` (two numbers a row).
  - `stimulus_objects` gains `size_deg`, `window_deg` and `light_xy`.
  - A missing number is NaN, and a missing pair is `[NaN, NaN]`.
  - The columns are written only where some entry of the file carries `size_deg`, `window_deg` or `light_xy`, as
    the trials table's record columns are. A record from before the build keeps today's tables exactly.
- **D4. A trial's `shaping`** is read as text (`TEXT`) and published as written in a trials column `shaping`, only
  where some line carries it. `drawn[name] == "full"` is already read (`DRAWN` takes any text), and the `drawn`
  column's description names it.
- **D5. The synthetic rig.** `RigRecordSpec.console: bool = False` turns the new shapes on, and it needs `fields`.
  Off by default, so every existing profile is byte-identical.
- **D6. No table and no contract field changes.** `core.ParameterChange` keeps each line whole (`schema/core.py:403`),
  and `schema/events.py::_keep_training_records` is unchanged. `SCHEMA_VERSION` stays 3. Only the two acts
  docstrings change, so `nwb_description.json` and `session_listing.json` are re-exported with new descriptions and
  no field added or removed. A `RunBlock.ended` of `"shaping"` is a value the type already holds, and its comment
  names it.
- **D7. `object_window`.** No published text here calls a trial's `object_window` degrees (a grep of `wl_preproc/`
  finds none). So the correction is in `window_deg`'s description: a setting may be a multiple of the object's
  radius, and `window_deg` is the degrees. `size_deg`'s description says the same of a `Scaled` size setting.

## Review Focus

1. **A withdrawal names who staged it.** `staged_by` is an actor with a name and an account. Neither may reach the
   NWB file. Its kind may. (Tasks 1 and 5.)
2. **A record from before the console build.** A session with no `size_deg`, `window_deg` or `light_xy` anywhere
   builds stimulus tables with exactly today's columns. (Task 3:
   `test_a_file_no_entry_carries_them_in_has_todays_rows`.)
3. **A `light_xy` or size wl-xcon wrote unreadably** (a single number, three numbers, text). It is none, said once
   per field, and the trial line is kept. (Task 2.)
4. **A revert that takes the training switch back.** It is a `shaping` row with a `moment`, not a `revert`. It is
   published with its moment and counted as no act. (Task 1.)
5. **A per-eye array.** Its objects' sizes and `light_xy` sit under `left` and `right`. They are read and published
   in the same rows as the objects' positions. (Tasks 2 and 3.)

---

### Task 1: The change log's three new kinds

**Files:**
- Modify: `wl_preproc/events/rigrecords.py`: the module docstring (:25-34), `SETTING_KINDS` (:200), the `KINDS`
  comment and tuple (:201-208), `_CHANGE_FIELDS` (:212-213), `ChangeFields` (:217-236) and `change_fields`
  (:239-263)
- Modify: `wl_preproc/nwb/rig_record.py::_add_changes` (:191-246)
- Modify: `wl_preproc/contracts/nwb_description.py::TrainingRecords` docstring (:225-233) and
  `wl_preproc/contracts/protocol.py::ListedTrainingRecords` docstring (:757-765)
- Modify: `docs/schemas/nwb_description.json`, `docs/schemas/session_listing.json` (regenerated)
- Test: `tests/events/test_rigrecords.py`, `tests/events/test_rigtiming.py`, `tests/nwb/test_writers.py`

**Interfaces:**
- Produces:
  - `SETTING_KINDS = ("set", "hand_back", "range", "revert", "form")`
  - `KINDS = ("set", "hand_back", "range", "revert", "clear", "drum", "training", "suggested", "declined", "form",
    "shaping", "withdrawn")`, wl-xcon's `record.KINDS` exactly, written out literally.
  - `ChangeFields.staged_by_kind: str | None = None`, the last field: `staged_by.kind`, read as `MAPPING` then
    `TEXT`, with fields `staged_by` and `staged_by.kind`.
  - A `staged_by_kind` column in `parameter_changes`, after `by_kind`.

- [ ] **Step 1: Write the failing tests** in `tests/events/test_rigrecords.py`:
  - `test_the_console_settings_kinds_are_known`. Four rows go through `change_fields`:
    - a `form` (change 9, `name` "search_size", `was` null, `now` `{"class": "Scaled", "fields": {"factor": 0.3}}`,
      `shape` "scaled", `event_code` 4130, `sequence` 1);
    - a `shaping` (change 10, `was` "on", `now` "full", `event_code` null);
    - two `withdrawn` rows sharing change 12 (`was` = `now` = 0.5, `event_code` null, `cid` "c-9f3a2b7e41",
      `withdrawn` `{"kind": "set", "now": 0.8}`, `staged_by` `{"kind": "member", "name": "Ada Staged",
      "account": "ada@lab"}`).

    The assertions:
    - the kinds are `["form", "shaping", "withdrawn", "withdrawn"]`, and `problems == []`;
    - `acts(fields) == 1`;
    - `fields[0].detail == '{"shape": "scaled"}'`;
    - `fields[2].detail == json.dumps({"cid": "c-9f3a2b7e41", "withdrawn": {"kind": "set", "now": 0.8}},
      sort_keys=True)`, and "Ada" is in no row's `detail`;
    - `[f.staged_by_kind for f in fields] == [None, None, "member", "member"]`;
    - `[f.strobed_nothing for f in fields] == [False, True, True, True]`.
  - `test_a_revert_of_the_switch_is_a_shaping_row_with_its_moment` (Review Focus 4): a `shaping` row with `moment` 0.
    Its kind is "shaping", `moment == 0`, and `acts == 0`.
  - `test_a_staged_by_it_cannot_read_is_none_and_said_once`: two rows with `staged_by` "Ada" give
    `staged_by_kind is None`, and the one problem is "2 change rows: `staged_by` is not an object or null, so it is
    read as none".
  - `test_a_form_is_counted_as_an_act_wherever_the_count_is_described`: "a form" is in
    `TrainingRecords.__doc__` and in `ListedTrainingRecords.__doc__`.
  - In `tests/events/test_rigtiming.py`, on the pattern of `test_a_change_that_strobed_nothing_is_timed_through_the_rigs_clock`:
    `test_a_form_is_timed_by_its_code_and_a_shaping_or_withdrawn_row_by_the_rigs_clock`. The form row (event_code
    4130) takes the time of the matching occurrence of 4130. The `shaping` and `withdrawn` rows are timed by the fit.
  - In `tests/nwb/test_writers.py`, `test_a_withdrawals_stager_is_published_by_kind_only` (Review Focus 1). It runs
    `add_rig_record` with a withdrawn row (`a_set(kind="withdrawn", staged_by_kind="member", detail=...)`) and a set
    row:
    - `staged_by_kind == ["member", ""]`;
    - the `kind` column's description holds "form", "shaping" and "withdrawn";
    - `detail`'s holds "shape", "cid" and "withdrawn";
    - `setting`'s holds "shaping";
    - the table's description holds "took back".
- [ ] **Step 2:** Run `PYTEST tests/events/test_rigrecords.py tests/events/test_rigtiming.py tests/nwb/test_writers.py`.
  Expected: FAIL (`staged_by_kind` unknown; the kinds are reported as unknown).
- [ ] **Step 3: Implement.** The table's description gains one sentence: "A withdrawn row changes nothing: a person
  took back a change before it applied, so it had no row of its own; `staged_by_kind` says who had sent it." The
  `kind` description names `form` (a person's draw or scaling, its `now` the form), `shaping` (the training switch,
  on, frozen or full) and `withdrawn`. Both docstrings say "since 2026-11-28 a form too". The `KINDS` comment cites
  wl-xcon `293a66e1`.
- [ ] **Step 4:** Run `.venv/bin/wlpp schemas export --out docs/schemas`, then `git diff --stat docs/schemas`.
  Expected: only `nwb_description.json` and `session_listing.json`, descriptions only (D6).
- [ ] **Step 5:** Run Step 2's tests, then `PYTEST tests/listing tests/cli/test_schemas_export.py
  tests/schema/test_events.py tests/responder/test_sessions_http.py REFERENCE_SET`, all with both interpreters
  (`REFERENCE_SET` because every file's `parameter_changes` gains a column). Expected: PASS.
- [ ] **Step 6: Commit** `feat(events): the change log knows form, shaping and withdrawn -- a form is a person's act,
  a withdrawal's stager is published by kind only` (files: `wl_preproc/events/rigrecords.py`,
  `wl_preproc/nwb/rig_record.py`, `wl_preproc/contracts/nwb_description.py`, `wl_preproc/contracts/protocol.py`,
  the two schema files, the three test files).

### Task 2: The trial line's switch, sizes, windows and lights

**Files:**
- Modify: `wl_preproc/events/rigfields.py` (the `XY` kind, beside `MAPPING` at :90)
- Modify: `wl_preproc/events/rigtrials.py`: the module docstring (:27-43), `Stimulus` (:113-137), `_stimuli`
  (:154-179), `OPTIONAL` (:182-192) and `RigTrial` (:196-221)
- Modify: `wl_preproc/nwb/trial_record.py::COLUMNS` (:28-52): the `shaping` column, and `drawn`'s description
  (:40-43)
- Test: `tests/events/test_rigtrials.py`, `tests/nwb/test_trial_record.py`

**Interfaces:**
- Produces:
  - `rigfields.XY = Kind(_xy, "a pair of finite numbers or null")`, which reads a two-item list of finite numbers as
    `tuple[float, float]`.
  - `rigtrials.CONSOLE_KEYS = ("size_deg", "window_deg", "light_xy")`.
  - `@dataclass(frozen=True, slots=True) class AsDrawn`: `size_deg: float | None = None`,
    `window_deg: float | None = None`, `light_xy: tuple[float, float] | None = None`.
  - New `Stimulus` fields, all defaulted:
    - `as_drawn: AsDrawn = AsDrawn()`
    - `objects_as_drawn: tuple[AsDrawn, ...] = ()`: parallel to `objects`, one per object
    - `eye_objects_as_drawn: tuple[tuple[str, tuple[AsDrawn, ...]], ...] = ()`: parallel to `eye_objects`
    - `carries: frozenset[str] = frozenset()`: the `CONSOLE_KEYS` the entry or any of its objects has
  - `OPTIONAL["shaping"] = TEXT`, and `RigTrial.shaping: str | None = None` after `color_realized`.
  - `trial_record.COLUMNS["shaping"]`.

- [ ] **Step 1: Write the failing tests** in `tests/events/test_rigtrials.py`:
  - `test_a_line_since_the_console_build_says_its_switch_and_a_full_source`:
    - a line with `shaping` "frozen" and `drawn` `{"contrast": "full", "search_size": 9}` reads `shaping ==
      "frozen"`, the `drawn` mapping as written, and has "shaping" in `carries`;
    - a line without it reads `shaping is None`, with "shaping" not in `carries`.
  - `test_each_stimulus_and_object_says_its_size_window_and_light` (Review Focus 5). A fix entry has `size_deg` 0.3
    and `light_xy` `[0.3, 0.6]`. An array entry has both null, and two objects: `{size_deg 2.4, window_deg 3.6,
    light_xy [0.3, 0.6]}` and `{size_deg 2.4, window_deg null, light_xy null}`. A per-eye entry's left objects carry
    `size_deg` 1.0. The reads are:
    - `fix.as_drawn == AsDrawn(0.3, None, (0.3, 0.6))`, and `fix.carries == {"size_deg", "light_xy"}`;
    - `array.as_drawn == AsDrawn()`, and `array.objects_as_drawn == (AsDrawn(2.4, 3.6, (0.3, 0.6)), AsDrawn(2.4,
      None, None))`;
    - `array.carries == set(CONSOLE_KEYS)`;
    - the per-eye entry's `eye_objects_as_drawn == (("left", (AsDrawn(1.0),)),)`.
  - `test_a_stimulus_before_the_console_build_carries_none_of_its_keys`: today's XC-399 entry (two objects) reads
    `carries == frozenset()`, `as_drawn == AsDrawn()` and `objects_as_drawn == (AsDrawn(), AsDrawn())`.
  - `test_a_size_or_light_it_cannot_read_is_none_and_said_by_its_path` (Review Focus 3): `light_xy` `[0.3]`,
    `size_deg` "big" and an object's `light_xy` `[1, 2, 3]`. Each reads as None, and the line is kept. The problems
    include "1 trial line: `stimuli[].light_xy` is not a pair of finite numbers or null, so it is read as none", the
    same for `stimuli[].size_deg` ("a finite number or null"), and the same for `stimuli[].objects[].light_xy`.
- In `tests/nwb/test_trial_record.py`:
  - `test_the_training_switch_is_a_column_where_lines_carry_it`: `shaping` of "on", "full" and a line without it
    give `["on", "full", ""]`. No column is written when no line carries it. The description names "on", "frozen"
    and "full".
  - Extend `test_the_drawn_column_names_every_source_the_rig_writes` with "full" and "form".
- [ ] **Step 2:** Run `PYTEST tests/events/test_rigtrials.py tests/nwb/test_trial_record.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The objects' fields are named `stimuli[].objects[].size_deg` (and the same for the eye forms, under
    `stimuli[].left.objects[]`).
  - `shaping`'s description: "The training switch as the trial was drawn, as the rig's record says: on, frozen or
    full ('' if not recorded, as before wl-xcon's console settings build)."
  - `drawn`'s description adds: "a person's form (the id of its form change), or full (the task's full version,
    while the training switch is at full)".
- [ ] **Step 4:** Run Step 2's tests, then `PYTEST tests/nwb tests/synth tests/schema/test_nwb_rig_fields.py`, with
  both interpreters (`Stimulus` and `RigTrial` gain defaulted fields only, so nothing else moves). Expected: PASS.
- [ ] **Step 5: Commit** `feat(events): each trial line's training switch, and each look's size, object window and
  drawn chromaticity` (files: `wl_preproc/events/rigfields.py`, `wl_preproc/events/rigtrials.py`,
  `wl_preproc/nwb/trial_record.py`, both test files).

### Task 3: The stimulus tables carry them

**Files:**
- Modify: `wl_preproc/nwb/stimuli.py::stimulus_rows` (:54-84) and its module docstring
- Modify: `wl_preproc/nwb/rig_record.py`: `TABLES["stimuli"]` (:59-78), `TABLES["stimulus_objects"]` (:79-85) and
  `add_rig_tables` (:139-146)
- Test: `tests/nwb/test_stimuli.py`, `tests/nwb/test_writers.py`

**Interfaces:**
- Consumes: Task 2's `AsDrawn`, `Stimulus.carries` and the `*_as_drawn` fields.
- Produces:
  - `stimulus_rows` keeps its signature.
  - When some matched entry has non-empty `carries`, every `stimuli` row gains `size_deg` (float, NaN) and
    `light_xy` (`[x, y]` or `[nan, nan]`). Every `stimulus_objects` row gains the same plus `window_deg`.
    Otherwise no row gains a key (D3).
  - `add_rig_tables` writes each `TABLES` column only when the table's rows carry its key. Today's columns are
    always in the rows, so they are always written.
- The descriptions, exactly:
  - `stimuli.size_deg`: "Its look's size as drawn at its place, degrees: a size scaled by eccentricity is its
    factor times that place's true eccentricity, so a size setting may give the factor and this the degrees (NaN
    for an array, whose objects carry theirs, for a look with no single size, or if none)."
  - `light_xy` (both tables): "The CIE 1931 [x, y] of the light it was drawn in, where a color set's light drew it,
    after its saturation and any move into the panel's gamut, as the rig wrote it: its drawn chromaticity at any
    saturation ([NaN, NaN] for an array's own entry, for a look in no color set's light, or if none)."
  - `stimulus_objects.size_deg`: "The object's size as drawn at its place, degrees (NaN if none)."
  - `stimulus_objects.window_deg`: "Its object window's radius, degrees: a trial's object_window setting may be a
    multiple of the object's radius, so this, not that setting, is the degrees (NaN where its array has no object
    window, or if none)."

- [ ] **Step 1: Write the failing tests.** In `tests/nwb/test_stimuli.py`:
  - `test_the_size_window_and_light_are_columns_where_some_entry_carries_them`. The fix has `as_drawn=AsDrawn(0.3,
    None, (0.3, 0.6))` and `carries={"size_deg", "light_xy"}`. The array has two objects,
    `objects_as_drawn=(AsDrawn(2.4, 3.6, (0.3, 0.6)), AsDrawn(2.4))`. Then:
    - `entries[0]["size_deg"] == 0.3` and `entries[0]["light_xy"] == [0.3, 0.6]`;
    - the array row's `size_deg` is NaN, and its `light_xy` is two NaN;
    - the objects' `window_deg` are `[3.6, nan]`, their `size_deg` `[2.4, 2.4]`, and the second's `light_xy` two NaN;
    - a matched trial with no stimuli adds no row.
  - `test_a_file_no_entry_carries_them_in_has_todays_rows` (Review Focus 2): today's test entries.
    - Every entry row's keys are exactly {trial_id, entry, stimulus, kind, frame, off, changed, drawn, unresolved,
      x_deg, y_deg, eccentricity_deg, polar_deg, target, n_objects, left, right}.
    - Every object row's are exactly {trial_id, entry, eye, object, x_deg, y_deg, eccentricity_deg, polar_deg}.
  - `test_a_per_eye_arrays_objects_carry_their_sizes_in_their_rows` (Review Focus 5): a left-eye object with
    `AsDrawn(1.0)` gives its row `size_deg == 1.0` and `eye == "left"`.
  - In `tests/nwb/test_writers.py`, `test_the_stimulus_tables_write_the_console_columns_only_when_their_rows_carry_them`:
    - with one carrying `stimuli` row, `module["stimuli"]["light_xy"].data[:]` has shape (1, 2);
    - a NaN pair reads back NaN;
    - the descriptions hold "drawn chromaticity" and "multiple of the object's radius";
    - without the keys, "light_xy" is not in `colnames`.
- [ ] **Step 2:** Run `PYTEST tests/nwb/test_stimuli.py tests/nwb/test_writers.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** Per-eye objects take their `AsDrawn` from `eye_objects_as_drawn`, in the order
  `stimulus_rows` writes their rows today.
- [ ] **Step 4:** Run Step 2's tests, then `PYTEST tests/nwb REFERENCE_SET`, with both interpreters
  (`add_rig_tables` and `stimulus_rows` are reached by every NWB build). Expected: PASS, and no file built from
  today's fixtures gains a column.
- [ ] **Step 5: Commit** `feat(nwb): the stimulus tables carry each look's size, object window and drawn
  chromaticity` (files: `wl_preproc/nwb/stimuli.py`, `wl_preproc/nwb/rig_record.py`, both test files).

### Task 4: The runs' new values, and every text they make false

**Files:**
- Modify: `wl_preproc/events/rigruns.py`: `BlockEnd`'s docstring (:58-65), which names `shaping`, `varied`'s `by`
  `scaled`, and the start row's `design` and `macaque_row`
- Modify: `wl_preproc/nwb/run_record.py`: `RUN_COLUMNS["color_sets"]` (:34-39), `BLOCK_COLUMNS["ended"]` (:63) and
  `BLOCK_COLUMNS["varied"]` (:70)
- Modify: `wl_preproc/nwb/trial_record.py::COLUMNS["color_realized"]` (:47-51)
- Modify: `wl_preproc/nwb/rig_record.py::TABLES["unopened_blocks"]`'s `ended` (:91)
- Modify: `wl_preproc/contracts/nwb_description.py::RunBlock`'s `ended` comment (:169-171)
- Test: `tests/nwb/test_run_record.py`, `tests/nwb/test_trial_record.py`, `tests/events/test_rigruns.py`

**Interfaces:**
- Produces the texts, exactly:
  - `ended` (all four places): "rule, time, fall_back, next, back, run_time, run or shaping (the training switch
    closed the stage the full task's start ends, or reopened it)".
  - `varied`'s addition: "each entry's by is condition, draw, person or scaled (a size scaled by eccentricity: its
    from, the form change, and its factor)".
  - `color_sets` and `color_realized` replace "a look's light is its color's xyz times its fraction, from the
    trial's params" with: "a look's light is its color's xyz times its fraction only at saturation 1; at any
    saturation its drawn chromaticity is its light_xy, in the stimuli tables. Each realization's macaque_row is its
    calibration's macaque luminance row, and a set's design is how it was generated (null for one written by hand)".

- [ ] **Step 1: Write the failing tests:**
  - `test_run_record.py::test_every_list_of_a_blocks_end_names_shaping` (parametrized over `BlockEnd.__doc__`,
    `BLOCK_COLUMNS["ended"]`, `dict(TABLES["unopened_blocks"][1])["ended"]` and `inspect.getsource(RunBlock)`): each
    names, as a whole word (`re.search(rf"\b{value}\b", text)`), every one of `rule`, `time`, `fall_back`, `next`,
    `back`, `run_time`, `run` and `shaping`.
  - `test_run_record.py::test_a_block_ended_by_the_switch_and_a_scaled_size_are_published_as_written`: a
    `BlockEnd(ended="shaping", varied={"search_size": [{"by": "scaled", "from": 9, "factor": 0.3}]}, ...)`.
    - The measured block's `ended == "shaping"`.
    - Its `record["varied"] == json.dumps(that varied, sort_keys=True)`.
    - "scaled" and "factor" are in `BLOCK_COLUMNS["varied"]`.
  - Extend the two tests that pin the colour text, keeping their "times its fraction" assertion:
    - `test_run_record.py::test_the_runs_table_says_where_each_stage_was_left_and_the_runs_color_sets` (:153);
    - `test_trial_record.py::test_a_new_luminances_color_realization_is_published_as_json` (:106).

    Each also asserts that "only at saturation 1", "light_xy" and "macaque_row" are in the text. The `color_sets`
    test also asserts "design", and that a `color_sets` holding `macaque_row` and `design` is published with both in
    its JSON.
  - `test_rigruns.py::test_a_block_ended_by_the_training_switch_is_read_as_written`: an end row's block end with
    `ended` "shaping" and that `varied` reads `ended == "shaping"`, with `varied` as written and no problem.
- [ ] **Step 2:** Run `PYTEST tests/nwb/test_run_record.py tests/nwb/test_trial_record.py tests/events/test_rigruns.py`.
  Expected: FAIL.
- [ ] **Step 3: Implement** the texts. No reading code changes: `ended` is already `TEXT` (`rigruns.py:130`), and
  `varied` and `color_sets` are already kept as JSON.
- [ ] **Step 4:** Run `.venv/bin/wlpp schemas export --out docs/schemas` and `git diff --stat docs/schemas`.
  Expected: nothing changes (a comment is not exported).
- [ ] **Step 5:** Run Step 2's tests and `PYTEST tests/nwb tests/schema/test_nwb_rig_fields.py`, with both
  interpreters. Expected: PASS.
- [ ] **Step 6: Commit** `fix(nwb): a block may end by the training switch, a size may vary by scaling, and xyz
  times a fraction is a look's light only at saturation 1` (files: the five modules, the three test files).

### Task 5: The synthetic rig writes them, and the file shows them

**Files:**
- Modify: `wl_preproc/synth/recipe.py::RigRecordSpec` (:294-315): `console`, and its check
- Modify: `wl_preproc/synth/peripherals.py`: `_stimuli` (:183), `_trial_fields` (:230), `write_rig_trials` (:250),
  `_start_fields` (:354) and `_end_fields` (:376). `_c3_changes` (:452) already writes any kind and its `detail` as
  given, and is unchanged.
- Modify: `tests/synth/test_peripherals.py` (`CONSOLE_RIG` beside `MIX_RIG` at :395)
- Create: `tests/schema/test_nwb_console_settings.py`, modelled on `tests/schema/test_nwb_rig_fields.py`

**Interfaces:**
- Produces:
  - `RigRecordSpec.console: bool = False`. With `fields` False it raises
    `PydanticCustomError("console_needs_fields", "console adds wl-xcon's console settings shapes to C3's, so it
    needs fields")` in a `model_validator(mode="after")`.
  - With `console`, the writer adds the following (D5):
    - `params`: `fix_color` `{"class": "ColorSetLight", "fields": {"color": "red", "colorset": "isohue", "fraction":
      0.5, "luminance": 20.0, "saturation": 1.0}}`, `search_size` `{"class": "Scaled", "fields": {"factor": 0.3}}`
      and `object_window` 3.0.
    - Each line's `shaping`: "full" on the priming run's lines, "on" otherwise. The priming lines' `drawn` gains
      `{"contrast": "full"}`.
    - `stimuli`:
      - `fix`: `size_deg` 0.3, `light_xy` [0.3, 0.6];
      - `target`: `size_deg` 1.0, `light_xy` null;
      - `search`: `size_deg` null, `light_xy` null, and each object `size_deg` 2.4, `window_deg` 3.6, `light_xy`
        [0.3, 0.6];
      - `mask`: both null.
    - Every run's start row's `color_sets`: `{"isohue": {"name": "isohue", "colors": {"red": [0.3, 0.6], "green":
      [0.25, 0.5]}, "apex": [0.3127, 0.329], "luminance_cd_m2": 20.0, "held": "macaque_2deg_LM1", "source":
      "synthetic", "design": {"space": "CAM16-UCS", "gray_lightness": 65.0, "chroma": 29.49}, "realized":
      [{"luminance_cd_m2": 20.0, "desaturation": 0.0, "xy": {"red": [0.3, 0.6], "green": [0.25, 0.5]}, "xyz":
      {"red": [150/13, 300/13, 50/13], "green": [100/9, 200/9, 100/9]}, "drive": {"red": [0.9, 0.2, 0.1], "green":
      [0.2, 0.9, 0.1]}, "macaque_row": [0.1, 0.8, 0.1]}]}}`. Each `xyz` is wl-xcon's formula at saturation 1 for that
      `macaque_row`, so a later piece that computes the light can reuse this fixture.
    - The end rows: the plain run's block end `ended` "shaping", and the balanced run's `varied` gains
      `"search_size": [{"by": "scaled", "from": 9, "factor": 0.3}]`.
  - `CONSOLE_RIG = {**MIX_RIG, "console": True, "changes": [*MIX_RIG["changes"], ...]}`. Its five new rows are
    places 9-13 and ids 9, 10, 11, 12, 12:
    - a `form` at trial 2 (`search_size`, `was` null, `now` the Scaled form, `event_code` 4130, `detail`
      `{"shape": "scaled"}`);
    - a `shaping` at trial 5 ("on" to "full", `event_code` None);
    - a `shaping` at trial 6 ("full" to "on", `event_code` None, `detail` `{"moment": 0}`);
    - two `withdrawn` rows at trial 6 with `change` 12 (`contrast` 0.5 to 0.5, and `fix_hold_s` 0.3 to 0.3), each
      `event_code` None, `detail` `{"cid": "c-9f3a2b7e41", "withdrawn": {"kind": "set", "now": 0.8}, "staged_by":
      {"kind": "member", "name": "Ada Staged", "account": "ada@lab"}}` (the second's `now` 0.4).

- [ ] **Step 1: Write the failing tests:**
  - `tests/synth/test_peripherals.py`:
    - `test_console_needs_fields`: `RigRecordSpec(console=True)` raises a `ValidationError` of type
      `console_needs_fields`.
    - `test_a_record_without_console_writes_none_of_its_keys`: MIX_RIG's `trials.jsonl` and `runs.jsonl` hold none
      of "shaping", "size_deg", "light_xy", "window_deg" or "color_sets".
    - `test_a_console_record_reads_back_whole`: with CONSOLE_RIG, the three readers report no problem. The
      readings are:
      - the priming run's trials have `shaping == "full"`;
      - fix's `as_drawn == AsDrawn(0.3, None, (0.3, 0.6))`;
      - the first run's `color_sets["isohue"]["realized"][0]["macaque_row"] == [0.1, 0.8, 0.1]`;
      - the change kinds end `["form", "shaping", "shaping", "withdrawn", "withdrawn"]`.
  - `tests/schema/test_nwb_console_settings.py`. Its fixtures are `recorded` and `built`, as in
    `test_nwb_rig_fields.py`, with `rig_record=CONSOLE_RIG` and a subject and date unclaimed across `tests/`
    (grep before choosing). The date is in the past.
    - `test_each_look_says_its_size_window_and_light`:
      - every `fix` row has `size_deg` 0.3 and `light_xy` (0.3, 0.6), as written;
      - `target`'s `light_xy` is two NaN;
      - the objects' `size_deg` are `[2.4, 2.4]`, `window_deg` `[3.6, 3.6]` and `light_xy` (0.3, 0.6);
      - "light_xyz_cd_m2" is in no table's `colnames`.
    - `test_each_trial_says_its_training_switch`: `shaping` is "full" on run 3's trials and "on" on the rest, and a
      run-3 `drawn` JSON has `contrast` "full".
    - `test_the_change_log_knows_the_console_kinds` (Review Focus 1):
      - the `kind` column ends with the five above, and `change` ends `[9, 10, 11, 12, 12]`;
      - `time_source` ends `["code", "rig_clock", "rig_clock", "rig_clock", "rig_clock"]`;
      - `staged_by_kind` is "member" on the two withdrawn rows and "" elsewhere;
      - `moment` of the second shaping row is 0;
      - "Ada" and "ada@lab" are in no text column of the table;
      - no note holds "which no build of wl-xcon this reader knows".
    - `test_a_block_ended_by_the_switch_and_the_runs_color_sets`:
      - the blocks' `ended == ["rule", "shaping", "next"]`;
      - run 1's `color_sets` JSON holds `macaque_row` and `design`;
      - block 1's `varied` holds `search_size` with `by` "scaled".
    - `test_a_form_is_a_persons_act`: `built.description["training_records"]["parameter_changes"] == 5`, and so is
      `session_entry(key)`'s.
- [ ] **Step 2:** Run `PYTEST tests/synth/test_peripherals.py tests/schema/test_nwb_console_settings.py`. Expected:
  FAIL.
- [ ] **Step 3: Implement** the flag and the writer. Each addition is under `if console:`, so a record without it is
  byte-identical (D5).
- [ ] **Step 4:** Run Step 2's tests, then `PYTEST tests/synth tests/schema/test_events.py REFERENCE_SET`, with both
  interpreters: every synthetic session passes through the changed writer. Expected: PASS.
- [ ] **Step 5: Commit** `test(synth): the synthetic rig writes wl-xcon's console settings shapes, and a built file
  shows each` (files: `wl_preproc/synth/recipe.py`, `wl_preproc/synth/peripherals.py`,
  `tests/synth/test_peripherals.py`, `tests/schema/test_nwb_console_settings.py`).

### Task 6: The records

**Files:**
- Modify: `docs/pending-wl-works-amendments.md`. The header's count (line 3) gains "one 2026-11-28". One new block
  after the header's rule, **"Added by the console-settings reader (2026-11-28)."** It says:
  - Two schema files are re-exported, descriptions only (D6): `nwb_description.json` and `session_listing.json`. No
    field is added or removed, so a pin is optional.
  - **`training_records.parameter_changes` counts a form as one act** (D1), in the listing and the description.
  - The NWB file gains:
    - the trials' `shaping` column;
    - `drawn` naming `full` and a form's id;
    - `stimuli` and `stimulus_objects` with `size_deg`, `window_deg` (objects) and `light_xy` (a 2-D column), only
      where the record carries them (D3);
    - `parameter_changes` with the kinds `form`, `shaping` and `withdrawn` and the column `staged_by_kind`, and
      `detail` holding `shape`, `cid` and `withdrawn` (D2);
    - a block's `ended` that may be `shaping`, here and in `RunBlock.ended`, and `varied` that may be `by`
      `scaled`.
  - A look's light in cd/m² is not published yet. It follows in a later piece, once wl-xcon writes each look's light
    luminance and set.
  - The colour texts are corrected, and `object_window` may be a multiple (D7).
- Modify: `HANDOVER-wl-expcontroller.md` (the lab's reply to wl-xcon). A new section, "# 2026-11-28: the console
  settings and withdrawn changes (XC-513, XC-529)", laid out as its "2026-11-07: the pop-out search's fields"
  section. It names what is read and where it is published (D1-D4), and says a withdrawal's `staged_by` is
  published by kind only. It ends with the spec's ask, as asked: beside each look's `light_xy`, its light's macaque
  luminance (cd/m², `fraction × luminance`) and its colour set's name.
- Create: `docs/handoffs/2026-11-28-console-settings-reader.md`, laid out as `docs/handoffs/2026-11-24-montages.md`:
  - the title and one paragraph;
  - Spec (`b5b3cce`), Plan (this file), Branch `feat/console-settings` (from this plan's commit), and the ledger
    `.superpowers/sdd/2026-11-28-console-settings/progress.md`;
  - "What was built" by task, with commit placeholders, and "Rulings and choices" (D1-D7) with "The requester's
    choices" (the light, 2026-11-28);
  - "The task reviews", with "For the whole-branch review" (Review Focus 1-5) and "The whole-branch review"
    (`FINAL_REVIEW`);
  - "Deferred minors";
  - "Carried to later parts":
    - the light in cd/m², once wl-xcon answers. Task 5's fixture already carries a realization's `macaque_row`;
    - the XC-543 pop-out revision reader (`trial_kind`, `stepped`, `found_by`, `odd_place`, the objects' `drawn`);
  - "The suite" (`SUITE_RESULTS`), "Deploy", and "Told to".

- [ ] **Step 1:** Write the three documents.
- [ ] **Step 2:** Run `grep -c "Added by the console-settings reader (2026-11-28)" docs/pending-wl-works-amendments.md`
  (expected `1`) and `grep -c "FINAL_REVIEW\|SUITE_RESULTS" docs/handoffs/2026-11-28-console-settings-reader.md`
  (expected `2`).
- [ ] **Step 3: Commit** `docs: the console-settings reader's records -- wl.works' notice, the reply to wl-xcon, and
  the handoff` (files: the three documents).

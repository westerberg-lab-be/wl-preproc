# The eye tracker placed by its analog copy on the NI

Under the NI's clock, an eye-tracker file that the NI's barcodes cannot place made the session tier D (amendments
51 and 57), and a partial box's eye data was used only inside its covered span (58). On recording days the eye
tracker's analog output reaches the NI's analog inputs. This piece matches the eye file's own columns to that copy,
window by window, and fits the pairs as barcodes are fitted. So the whole file is placed on the NI's clock with no
barcode, and a session with the eye tracker under the NI can be tier N. Where a partial box's barcodes also place
the file, the two measure the copy's lag. This is amendment 65's eye half. The camera half is carried (amendment 83).
- **Spec:** `docs/superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md`, amendments 77-84 (the
  design), amendments 85-86 (made while building). Where 85-86 differ from 77-84, they bind.
- **Plan:** `docs/superpowers/plans/2026-11-21-eye-by-analog-copy.md`, seven tasks, built by subagents.
- **Branch:** `feat/eye-analog-copy`, from `plan/eye-analog-copy` (`fc33ebe`, the plan), from
  `spec/eye-analog-copy` (`cac8540`), from `main` at `291bbea`. `main` was merged in after the move to the lab's
  GitHub account (`37f5ef3`, over `c111f8f`).
- **The ledger:** `.superpowers/sdd/2026-11-21-eye-by-analog-copy/progress.md` holds the full wording of every
  ruling below.

## What was built

By task, each with its commits.
- **Task 1, the synthetic copy** (`fc33ebe..226240b`: `863adaf`, `226240b`). `synth/recipe.py::EyeCopy`, and
  `SessionRecipe.eye_copy`, None by default, so every earlier recipe is byte-identical (the eye file's hash joined
  the byte-identity test).
  - `synth/spikeglx.py` writes the copy onto NI XA channels (`snsMnMaXaDw=0,0,n,2`) only when a recipe has one.
  - `synth/ohdpi.py::eye_traces` carries the trace draws, unchanged in order, so the copy and the eye file share
    them.
  - The copy is 0.7 X + 0.3 Y of the left P1-P4 vector, bent by `tanh`, scaled and offset. Each frame is held from
    its arrival, in arrival order (R6), with the instrument paper's delays (median 1.1 ms, 2% at 10-50 ms), a lag,
    an optional stall, a moving average, noise and the NI's rails. `source="noise"` and `source="flat"` stand for a
    channel with no copy.
  - Tests: `tests/synth/test_eye_copy.py`. The anti-tautology test holds that no single candidate column explains
    95% of the copy (R4).
- **Task 2, the matcher** (`226240b..39ddc32`: `d43f46a`, the merge `37f5ef3`, `8f3c122`, `39ddc32`).
  `timebase/analog_copy.py`, pure numeric: `find_channel`, `match_copy`, `CopyMatch`, `NoCopy` and `CopyRefused`,
  and the constants of `docs/hardware-checks.md` 17.
  - The anchor window is found against the whole channel by FFT. Later windows are walked within
    `RUNNER_UP_EXCLUSION_S` of the line so far.
  - A window gives a pair only if it is distinct: its best shift reaches `MIN_CORRELATION` and beats the best rival
    peak by `MIN_PEAK_MARGIN` (R9). A window more than a tenth at the rail gives no pair (R10).
  - Pairs off the line are dropped and the line refitted, up to `MAX_DROPPED_FRACTION`, and the stretches are
    named.
  - Each family of columns is fitted jointly (R7, `8f3c122`). The synthetic eye gains fixations and saccades when
    a recipe has a copy (R8, `39ddc32`).
  - Tests: `tests/timebase/test_analog_copy.py`.
- **Task 3, reading the files** (`39ddc32..563bb2a`: `bc0bd2c`, `6e0babc`, `563bb2a`).
  - `analog_copy.FAMILIES` (R12), `eye_candidates`, `read_xa_channels`, `place_eye_by_copy` and `CopyPlacement`.
  - The NI's XA channels are streamed through `channels.column_chunks`, which gains start and stop rows, and
    decimated to block means, each stamped at its block's centre. `NidqMeta` gains `n_xa_channels`.
  - The search reads a stretch of every channel; only the chosen channel is read whole.
  - A copy found but too short whole is refused (R13, `6e0babc`).
  - The search is aimed by the two files' clocks (amendment 85, `563bb2a`): `parse_openiris_stamp`,
    `clock_offset_s`, `CLOCK_SLACK_S`.
  - Tests: `tests/timebase/test_analog_copy_files.py`.
- **Task 4, the fit, the lag and the placement** (`563bb2a..6e7de58`: `7bfc06c`, `efdab09`, `6e7de58`).
  - `timebase/segments.py::NO_ANALOG_MATCH`. `schema/timebase.py::_TIME_SOURCE_ENUM` gains `analog`.
  - `SystemTimebase.Copy`, a part table: `outcome` (`placed`, `failed`, `absent`), `refusal`, `ni_channel`,
    `signal`, `n_pairs`, `n_dropped`, `lag_s`, `lag_measured`, `lag_source_datetime`, `agreement_us`, `offset_s`,
    `residual_us`, `first_sample`, `placed_file`, `placed_rows`, `placed_bytes`, `search_unaimed` and `rig`.
    `search_unaimed`, added in the final review's fix wave, is why the two files' clocks did not aim the channel
    search (amendment 85), null where they did.
  - `SystemTimebase._make_by_copy` follows the outcome table of the plan's global constraints. Other systems take
    the barcode path, factored out as `_by_barcodes`.
  - The lag: `_cross_check` measures it over the eye file's own barcodes in the span (F4); otherwise
    `_carried_lag` carries it (P2), or it is 0 and unmeasured.
  - `copy_fails` makes `TimingProvenance` D for a measured lag out of bounds or a copy that disagrees.
  - `core.Segment.make` gains an analog branch before SpikeGLX's identity branch. `Segment._place_by_copy` writes
    the stored placement for the file it placed alone (`copy_identity`, R17), and keeps amendment 76's
    `no_shared_barcode`.
  - The copy is matched once a session, in `SystemTimebase` (`efdab09`). A stall's refusal names its share and
    stretches (F3).
  - Tests: `tests/schema/test_eye_by_copy.py`, seventeen synthetic sessions of 252 s in one module fixture, landed
    in two daemon passes.
- **Task 5, eye data past the span** (`6e7de58..995027a`: `995027a`). `eye/span.py::eye_span` is None, the whole
  file, for an eye whose `time_source` is `analog`, and otherwise the covered span. `EyeCalibration`, `EyeQuality`,
  `EyeValidity`, `EyeDetection` and `nwb/gather.py::_eye` take it. `schema/coverage.py` is unchanged.
- **Task 6, what the file and the report say** (`995027a..73de9ab`: `73de9ab`).
  - `nwb/timebase.py`: `system_clocks.time_source`'s text gains `analog`, for both statuses (3b-3's R15 form).
    `worst_residual`, `fitted_rate`, `residual_rms` and `first_sample` say what they hold for the eye tracker
    placed by its copy.
  - `nwb/gather.py::_copy_notes` writes into the description's `notes` how the eye was placed: the channel, the
    signal, the pairs, the lag and its footing (measured here, measured in another session, or unmeasured), any
    dropped stretches, or the refusal and what placed the file instead. `_span_notes` no longer says a copy-placed
    eye's data outside the span is left out.
  - `cli/report.py`: the section "Eye placed by its analog copy (24 h)", built by `_copy_rows` and `_copy_lines`,
    names each copy refused and each measured lag more than one frame from the rig's previous one. The final
    review's fix wave adds a line for each absent copy whose search was not aimed, and one for each copy whose
    measured lag fails its checks (`copy_fails`), which makes the session D.
  - `Copy.dropped_stretches`, and `analog_copy.stretches_text`.
- **Task 7: these records.** Amendments 85 and 86. Rows 15-18 and 16a of `docs/hardware-checks.md` cite the built
  constants and the `Copy` table, and rows 9a, 15a, 16b, 17a, 18a and 18b are new. The tier and `no_analog_match`
  lines in `docs/ops/lab-host-protocol.md`, two lines in wl.works' NI entry in
  `docs/pending-wl-works-amendments.md`, and this handoff.

## Rulings and choices

In the order made, each with what it costs if wrong. Where the ledger states no cost, the cost given is the
ruling's consequence, so marked.
- **P1, pre-flight:** Task 4's measured-lag expectation adds the moving average's group delay to the set lag and
  the jitter's median. Cost if wrong: a test bound loosened by 0.5 ms. Amended at Task 4, below.
- **P2, pre-flight:** a lag is carried by the copy's own checks, not by tier (amendment 86). It was the builder
  check's call, not the requester's. Cost if wrong: a lag from a session that is D for an unrelated reason is
  reused, though its own measurement passed.
- **P3, pre-flight:** a session placed before its rig's earlier measuring session is processed keeps lag 0,
  unmeasured, and is not placed again (amendment 28). Cost if wrong: within one daemon pass, a later session may
  publish with the unmeasured label though a measurement exists. Deleting its timing rows places it again.
- **R1, Task 1:** the copy's correlation with P1-P4 X is held to 0.9, not 0.95. A 0.7 X + 0.3 Y mix of
  uncorrelated axes tops out near 0.95 even without noise. Cost if wrong: none for the code; `MIN_CORRELATION`'s
  margin on synthetic windows is thin.
- **R2:** `gain_v` is volts per pixel. Cost if wrong: one field's docstring.
- **R3:** `source="flat"` is exactly `offset_v`, with no noise: a stuck DAC. Cost if wrong: one test case less
  realistic.
- **R4:** the anti-tautology test asserts that the best single-candidate fit explains less than 95% of the copy's
  variance (R² < 0.95, on 10-frame means), and it fails with the Y weight at 0. Cost if wrong: one threshold to
  retune.
- **R5:** the copy shows the newest frame arrived, a late frame never holding later ones back. Superseded by R6.
  Cost if wrong: if the real DAC is strictly in order, real tails are heavier than the synthetic.
- **R6:** the copy shows the last frame to arrive, in arrival order, so a slow frame is shown late for its own
  interval and holds nothing back. Cost if wrong: brief out-of-order values that a DAC that skips frames would not
  show.
- **R7, Task 2:** each NI channel is fitted on a family of the eye's columns jointly (amendment 86). Cost if wrong:
  more compute per shift, and a changed signal vocabulary.
- **R8:** with a copy set, the synthetic eye gains fixations and saccades, about three a second. Cost if wrong: one
  more generator change in Task 1's files.
- **R9:** the runner-up is the best rival peak more than `RUNNER_UP_EXCLUSION_S` away, not any shift there, which
  read literally is the main peak's own shoulder. Cost if wrong: a periodic trace's rival peak must still be caught,
  and its test stays.
- **R10:** each window's grid uses the line's rate, and a window more than a tenth at the rail gives no pair. Cost
  if wrong: one more named constant.
- **R11:** derived whole-channel arrays at 1 kHz are within the memory rule, which bounds the raw NI file. The peak
  is about 3.5 GB for 12 h at two columns. Cost if wrong: per-row spectra in the anchor loop, which are cheap, if
  `serv`'s memory bites.
- **R12:** the family names are a production constant, `analog_copy.FAMILIES`, which `eye_candidates` is built
  from and the 32-character test checks. Cost if wrong: none.
- **R13, Task 3:** a NoCopy from `match_copy` after `find_channel` found the channel is `failed` (amendment 86).
  Cost if wrong: a rare absent copy is reported as failed in the daily report.
- **The requester's choice, 2026-11-21:** aim the search by the two files' clocks (amendment 85).
- **R14:** the eye's wall start is the file name's stamp alone, never its mtime. Cost if wrong: renamed files fall
  back to `d = 0`.
- **R15** (the implementer's, accepted): the NI's interval is centred on `max(d, 0)`, correcting the draft. Cost if
  wrong: none; a mutation to the draft's formula fails its test.
- **R16** (the implementer's, accepted): the fallback reads the NI from 0 to `SEARCH_PREFIX_S + CLOCK_SLACK_S`, one
  rule at `d = 0`. Cost if wrong, by consequence: the fallback reads 120 s more of each channel than the search
  did before amendment 85.
- **P1, amended at Task 4's dispatch:** the lag expectations are the generator's whole delay, about 7.13 ms for a
  5 ms set lag, and `copy_only`'s bound is that delay plus 0.5 ms (amendment 86). Cost if wrong: test bounds only.
- **R4-1, Task 4:** `copy_absent` is a box with its copy channel carrying noise, compared against
  `copy_absent_plain`, the same recipe with no copy. Cost if wrong: one extra session.
- **R4-2:** with no box, `copy_noise` and `copy_flat` are `unfitted_system`, not `no_barcode` (amendment 86). Cost if
  wrong: none; Tasks 5 and 6 consume nothing different.
- **R4-3:** `copy_disagree` warps the NI's copy channel by 3000 ppm after generation, since the recipe has no clock
  field. Cost if wrong, by consequence: a recipe field would be cleaner (a deferred minor).
- **R4-4:** `copy_negative` is generated at lag 0 and its copy channel shifted 4 ms earlier, since `EyeCopy.lag_s`
  is at least 0. Cost if wrong: as R4-3.
- **R4-5:** a lag is measured only where at least one copy pair lies inside the span. The cross-check runs only on
  a lag measured here. Cost if wrong, by consequence: a box that stopped before the first window's centre carries
  rather than measures.
- **R4-6:** agreement is taken from the two fitted lines at the span's ends, since the synthetic span holds one
  pair. Sharpened by F4. Cost if wrong: as F4.
- **R4-7:** a carried lag is marked measured, with `agreement_us` null, so it is never itself a source. A rig of
  `''` carries nothing. Cost if wrong, by consequence: a session whose manifest cannot be read is always unmeasured.
- **R4-8:** `copy_earlier` and `copy_later` sit on `copy_partial`'s rig, so "latest" and "at or before" each have a
  test that can fail. Cost if wrong: two extra sessions.
- **R4-9:** `copy_stall_box`, a box with a 25% stall, stands for the outcome table's "failed, with barcodes" row.
  Cost if wrong: one extra session.
- **R4-10:** `Segment.make` matched the copy again. Superseded by fix round 1's finding 1: the copy is matched once,
  and `Copy` stores the placement.
- **R4-11:** each session has a rig of its own, but for the carry group and the bad group. Cost if wrong: none; it
  keeps unrelated sessions from carrying lags.
- **Task 4's review, re-graded by effect:** agreement over the NI's span, not the eye file's barcodes, was Important.
  An eye file starting inside the box's span extrapolates its barcode line and can make a sound session D. It
  joined the fix round, as F4.
- **F3:** a stall's refusal counts pairs against a Theil-Sen slope through the median residual, refitted to the
  pairs within `RESIDUAL_BOUND_US`. scipy's own intercept read 74% of the pairs off the line. Cost if wrong, by
  consequence: the refusal's wording, not its decision, which is unchanged.
- **F4:** the lag's pairs and the cross-check are held over the eye file's own first and last shared barcode,
  within the span (amendment 86). Cost if wrong, by consequence: an eye file whose barcodes cover less of the span
  is checked over less of it.
- **R17:** `Copy` records the file it placed, and `Segment.make` applies the stored placement to that file alone
  (amendment 86). Cost if wrong: a replaced eye file needs its timing rows deleted to be placed again.
- **R2-1:** the file's identity is its path, rows and size, not its mtime, which copies and rehydration reset. Cost
  if wrong, by consequence: a file rewritten in place with the same rows and size keeps its placement.
- **R2-2:** the note naming both files goes to the log. `RejectedSegment.reason` keeps fixed reason words, as
  `no_strobe_anchor` does. Cost if wrong: an additive column later.
- **Task 5, ruling 1:** `copy_partial`'s calibration windows lie after the span (from 10.1 s), since held gaze inside
  it removed the span's one copy pair. Cost if wrong: no test has calibration windows inside the span of a
  copy-placed file (`docs/hardware-checks.md` 15a).
- **Task 5, ruling 2:** `copy_absent` keeps the partial box's windows, its copy being noise. Cost if wrong: none.
- **Task 5, ruling 3:** `copy_stall_box` has no windows, so its "still cut" check is `eye_span` against the
  clock's span, not a stage's rows. Cost if wrong: a deferred minor below.
- **Task 5, ruling 4:** the NWB test builds the canonical file through `accept` and `daemon.run_once`, as 3b-3's
  does. Cost if wrong: none.
- **Task 6, `dropped_stretches`:** the dropped stretches were not stored, so `Copy` gains `dropped_stretches`,
  clipped to 255 characters. Cost if wrong: a database rebuild, which the new part table needs anyway; the clip
  is a deferred minor.
- **Task 6, the R15 form:** `time_source` states `analog` for both statuses, and the four other NI texts are
  extended, since they would otherwise read false for a copy-placed eye. Cost if wrong: four pinned texts changed;
  wl.works is told at the merge.
- **Task 6, `_span_notes`:** a copy-placed eye's note that its data outside the span is left out, false since Task
  5, is no longer written. Cost if wrong: none.
- **Task 6, the barcode count:** `Segment.n_barcodes` is 0 for a copy-placed file. The barcodes that measured the
  lag are named only where the lag was measured here. Cost if wrong: a carried or unmeasured file shows 0 with no
  text (a deferred minor).
- **Task 6, the report:** an absent copy gets no line. A failed copy with no barcode fit reads as rejected
  `no_analog_match`, inferred from the fit's status and source, not from a stored rejection. A lag jump compares
  only a lag measured here with the rig's previous measured lag that passed its checks. Cost if wrong: the wording
  of a line (a deferred minor).

### The requester's choices

- **2026-11-21, the design** (amendment 77's preamble): the eye tracker only; slide the trace along its copy; publish
  with the lag labelled unmeasured until a real session measures it; the behaviour played back and approved.
- **2026-11-21, what the DAC puts out** (78a): "not sure; try them all".
- **2026-11-21, the 1c-4 conflict** (84): told that the 1c-4 timebase design's §2.1 rejected the analog output, and
  chose to supersede it for this fallback alone.
- **2026-11-21, execution:** subagent-driven.
- **2026-11-21, the move to the lab's GitHub account:** checkpoint after Task 2's review, and push `spec/`, `plan/`
  and `feat/eye-analog-copy` before the move (the requester chose to push). Resumed after it: `origin` is
  `github.com/westerberg-lab-be/wl-preproc`, and `main`'s CI was green there.
- **2026-11-21, the start times** (85): asked how far apart the eye tracker and SpikeGLX start, chose "It varies;
  aim by clocks".

## The task reviews

Every task had its own review. Tasks 2, 5 and 6 were approved on their first review; Task 2's fix round came
before it, on the implementer's own concerns.
- **Task 1** (Sonnet): needs fixes.
  - I1: the anti-tautology test passed under a pure-X copy. Settled by R4.
  - I2: the effective jitter was about ten times the brief's, a running maximum cascading a slow frame onto the
    frames behind it. Settled by R5, then R6.
  - ⚠️: the eye file's bytes were unpinned while `write_ohdpi` was refactored. Its hash joined the byte-identity
    test. A re-review was clean.
- **Task 2** (Opus, after a fix round sent before review): approved, with four Minor findings, two ruled (R11,
  R12). The fix round carried R7 and R8, to a fresh implementer, since the first's context was lost at the
  migration.
- **Task 3** (Opus): approved, with one Important finding and five Minor.
  - I1: the search prefix limits both directions. Settled by the docstring, then by amendment 85, the requester's
    choice.
  - Minor 2 became R13, and minor 3 (the WARNING untested) was taken in the same round.
  - A re-review of both rounds found items 1-4 addressed, and five new Minor findings, deferred.
- **Task 4** (Opus): needs fixes, with three Important findings and six Minor.
  - I1: `Segment.make` matched the whole file again. Settled: the copy is matched once, and `Copy` stores the
    placement (`test_each_eye_file_is_matched_to_its_copy_once`).
  - I2: the `no_shared_barcode` guard was untested. Settled by a stub test.
  - I3: a stall's refusal read "100%" and named no channel or stretch. Settled by F3, with the channel and signal
    prefixed.
  - Minor 4, re-graded Important: settled by F4.
  - A re-review found a new Important finding: the stored placement was not tied to its file. Settled by R17, R2-1
    and R2-2. A second re-review (Sonnet) was clean, with two Minor.
- **Task 5** (Sonnet): approved, with two Minor and a fixture note, which became hardware check 15a.
- **Task 6** (Sonnet): approved, with five Minor. The four NI texts, the `dropped_stretches` column and the
  `_span_notes` fix were judged justified beyond the brief.

### For the whole-branch review

Named checks the task reviews carried forward:
- **Task 4:** P3 and amendment 28 are untested; they hold by structure. Task 4's 3.13 runs covered only its own
  files.
- **Task 5:** whether repopulating `SystemTimebase` invalidates the downstream eye tables when `time_source` flips
  between `barcode` and `analog`. And `schema/coverage.py:152`'s `fetch1("time_source")` over a key that may hold
  several systems (pre-existing).
- **Task 6:** whether any NWB text shows `Segment.n_barcodes`, which is 0 for a copy-placed file.
- **Task 4's fixture cost:** seventeen sessions of 252 s add about 8 minutes to every `tests/schema/` run, to weigh
  against CI time.

### The whole-branch review

One whole-branch review (Opus) of `c111f8f..4f3bf8d`: no Critical finding, two Important, seven Minor.

- **I1.** The fallback to `d = 0` was logged at INFO, which nothing in `wl_preproc` shows. Fixed in `115aad7`: a
  WARNING, the nullable `Copy.search_unaimed` holding the reason, and a daily-report line for an absent copy whose
  search was unaimed.
- **I2.** A copy whose measured lag failed its checks made the session tier D with nothing a person reads saying
  why. Fixed in `115aad7`: the report's section names the lag, the bound it broke, the agreement against one frame,
  and the tier. Such a copy gets that line alone, not also a lag-jump line.
- **M3**, the stale "amendment 85, draft" citations, was folded into the same fix.
- **M7.** The NI's texts for `time_source`, `worst_residual_us`, `fitted_rate_hz`, `residual_us_rms` and
  `first_sample` change for every NI session, against the plan's "unchanged". This was accepted: under 3b-3's R15
  form each text must read true for `analog`. It is announced in `docs/pending-wl-works-amendments.md`.
- A scoped re-review of the fix wave found every finding addressed. It also found that the new heading, "## Eye
  placed by its analog copy", was prefix-matched by the tests' `_section("Eye")` helpers. Fixed in `a0246fa`: every
  copy of the helper matches a heading exactly.
- The first full suite after the fix wave failed three tests on each interpreter, all tests' own:
  `test_intan_by_strobes.py` and `test_partial_sync_box.py` still pinned 3b-3's wording of the five NI texts
  (`tests/nwb/test_writers.py` pins the new wording), and the report-line tests wrote `zz000001`, a value
  `tests/identities.py` reserves. Fixed in `170c5ed`.

The review's remaining minors are below, under "Final review".

## Deferred minors

**Task 1**
- `eye_copy_frame_arrivals_s` re-derives the frame count and drift rather than sharing a helper.
- The drift path and the smoothing's zero-padded edges are untested. The stall is not validated (start before
  stop, extra at least 0). The clip test checks only the upper rail. A stray `_generate_plain` wrapper.
- The delay test pins the arrival delay over all frames, not the time-weighted delay of the frames shown. The
  arrival-order hold is not directly asserted.
- The eye file's hash is checked on macOS only; Linux CI skips it. A tolerance comparison of the parsed file would
  close it.
- Unverified that `eye` and `drift` are the only recipes with an eye tracker.

**Task 2**
- `find_channel` takes about 40 s on 600 s of 16 channels, unbatched.
- Pre-existing on `main`: `synth/spikeglx.py:34`'s unused import of `electrode_rows` (ruff F401).
- The synthetic eye keeps its sinusoids, which give joint rivals an `R` of 0.71-0.76 (margins 0.17-0.25 against
  0.1). Pure saccadic gaze would restore headroom.
- The module docstring's memory estimate is wrong for two columns: about 3.5 GB at the peak, not 1.4. A per-row
  spectrum in the anchor loop would cut it.
- `tests/synth/test_eye_copy.py:312` calls `EYE_PROCESSING_MEDIAN_S` "its mean".
- `_MAX_MISSING_FRACTION` is untested; the `no_missing_rule` mutation survives.
- `find_channel`'s windows are at rate 1, not the line's: about half a sample at 100 ppm, commented, and only
  counted.

**Task 3**
- `NidqMeta.n_xa_channels` needs no default.
- Lost-tracking zeros are passed to the matcher raw (`docs/hardware-checks.md` 16b).
- The `.meta` is read three times. The whole read's `start_s` is discarded. The prefix read makes one memmap pass
  per channel.
- No test runs `place_eye_by_copy` on a file longer than `SEARCH_PREFIX_S`; the synthetic session is 252 s.
- `place_eye_by_copy`'s docstring overstates the clock's sensitivity: the copy is still found up to about ±520 s of
  clock error, absent only beyond about `CLOCK_SLACK_S` plus 400 s.
- `find_channel`'s NoCopy message (`analog_copy.py:284`) still says "in the first N s", though its windows start at
  `eye_from_s` when aimed.
- `_wall_difference_s`'s branch for one aware and one naive time is dead in production.
- No found-case test carries a clock error within the slack.
- Long lines: `analog_copy.py:250` (135 characters) and `test_analog_copy_files.py:430` (121); E501 is off.
- R13 converts any NoCopy from `match_copy`, unguarded if `match_copy` gains another NoCopy reason.

**Task 4**
- `place_eye_by_copy`'s docstring says a session with no box fails `no_barcode`; it is `unfitted_system` (R4-2).
- An NI session with an eye tracker and no XA channels logs a WARNING ("0 analog channels") and parses the eye's
  columns first. Check `meta.n_xa_channels == 0` before `eye_candidates`.
- The fixture's cost: dropping `copy_flat`, shortening `copy_two_files` and `copy_absent_plain`, unit-testing the
  carry's selection to retire `copy_earlier` and `copy_later`, and 215 s sessions would take about a third off.
- `Segment.n_barcodes` is 0 where `SystemTimebase.n_barcodes_matched` is not, on a row whose lag was measured here.
  Task 6's texts explain it there alone (see Task 6).
- An exact equality, `copy["lag_s"] == source["lag_s"]`: the same stored double, acceptable.
- `copy_disagree` and `copy_negative` edit the NI channel after generation; a recipe field would be cleaner.
- The robust count's wording can contradict the refusal when greedy dropping masks outliers ("2 of 25, 8%", or
  "0 of N over no stretch").
- `theilslopes` builds N × N arrays (about 0.5 GB at 4,320 pairs), on the refusal path only.
- `_cross_check` and `_make_by_copy` repeat `fit_pairs_across` and `fit_offset_to_pairs` on the same pairs. The
  refusal text may pass 255 characters and be clipped. Intan's `Segment` still runs `place_intan` again per 12 h
  file, the same cost pattern.
- The mismatch WARNING repeats on every `Segment` pass over the file.
- No end-to-end `Segment.make` test with a second eye file on disk; unit and recording-side tests only.

**Task 5**
- `tests/schema/test_eye_by_copy.py:62-129`: an inserted block splits `LAG_BOUND_S`'s comment from its constant.
- No assertion of `EyeQuality`'s own values. `copy_only` has no calibration or detection check. `copy_stall_box`'s
  check is `eye_span` against the clock's span alone.

**Task 6**
- `dropped_stretches` is clipped mid-number at 255 characters ("130.0-14…"). Cut at the last whole stretch and add
  "and N more"; untested.
- The barcode count is explained only for a lag measured here. A carried or unmeasured copy-placed segment shows 0
  with no text.
- The report's `no_analog_match` wording is inferred from `(fit_status, time_source)`, not a stored rejection row.
- The new report section is not in `Readings`, following the precedent of `_eye_rows` and `_syncbox_restarts`.
- `eye_span` keys on `time_source` alone; `_span_notes` also requires `fitted`. They agree in every real state.

**Final review**
- M1: `_next`'s band-edge shoulder can give a pair up to 50 ms off. It is dropped, but it uses the drop budget and
  names a false stretch. Reject a band-edge peak unless it is a local maximum.
- M2: `Copy.first_sample` and `Segment.first_sample` hold an eye row, though the column comment says native index
  (the NWB text says row).
- M4: the whole-channel re-anchor faces about 50 times the prefix's rivals, so with R13 a real copy may report
  failed on a 12 h file. Seed it from the search's shift. Watch under hardware check 17.
- M5: R17's note should also name the eye tables, which key on `pipeline.Session` and do not cascade.
- M6: the unmeasured lag's note, "of the order of 4-5 ms", understates: 4-5 ms plus the filter, 7.1 ms of delay in
  all on the synthetic copy. The text is the brief's, verbatim; revisit with hardware check 17.
- No schema-level test of a null `search_unaimed`; every fixture is unstamped. A placement-level test covers it.
- An unaimed reason that embeds a long eye-file stem is clipped at 255 characters and loses its tail.
- The handoff's Task 5 and 6 ruling numbers come from the task reports, not the ledger.
- The handoff's costs marked "by consequence" are inferences.

**Found while writing these records**
- The code still cites "amendment 85, draft" (`analog_copy.py:154`, `:755`;
  `tests/timebase/test_analog_copy_files.py:310`). Amendment 85 is now written. Fixed in the final review's fix
  wave.
- The fallback's INFO line (`clock_offset_s`): by `place_eye_by_copy`'s own docstring, nothing in `wl_preproc`
  configures logging, so an INFO line is never seen. A session whose search fell back to `d = 0` says so nowhere a
  person reads. Fixed in the final review's fix wave: a WARNING, `Copy.search_unaimed`, and a report line for an
  absent copy.

## Carried to later parts

- **Amendment 83, the camera half of amendment 65.** Until the camera package writes each video file's start and
  end on the camera computer's wall clock, a camera file with no barcode under the NI still fails the session
  (57), and our sidecar contract (`contracts/sidecar.py`) still requires `trigger_source` `syncbox`.
- **The ask to wl-camacq.** wl-orchestrator registers it `named`, with no repository yet
  (`registry/packages/wl-camacq.yaml` at `333ed94`). That registration lists wl-preproc's first request: each
  frame's host timestamp on lab time, the computer's sync state, a frame identity, and the row shape sent before it
  is built. Amendment 83's ask, each video file's start and end wall time in the sidecar and the sidecar's
  `trigger_source`, is not among those four items. It is carried, not sent: there is no repository to send it to.
- **The hardware.** Rows 15-18b of `docs/hardware-checks.md`, and 9a.

## The suite

The full suite (`tests/`), with every reference set (NSLR, BMD and Boost, Andersson, the OpenIris recording), at
`170c5ed`:
- **3.11:** 3441 passed, 2 skipped (U'n'Eye's numba OpenMP; no `WLPP_BHV2_SAMPLE`), 1 deselected (a `slow`-marked
  test, excluded by `addopts`), 1 xfailed.
- **3.13:** 3441 passed, 3 skipped (Kilosort not installed, twice; no `WLPP_BHV2_SAMPLE`), 1 xfailed.

The same skips as 3b-3's merge. After it, only this handoff changed.

## Deploy

- **A rebuilt database.** `_TIME_SOURCE_ENUM` gains `analog`, and `SystemTimebase` gains the part table `Copy`.
  Development databases are rebuilt, never migrated.
- **Every eye tracker under the NI with one file** now gets a `Copy` row, and a channel search: absent where the NI
  recorded no XA channel. On a 12 h file the search reads at most `SEARCH_PREFIX_S` plus twice `CLOCK_SLACK_S`
  of each XA channel, and the chosen channel is then read whole, decimated.
- **`--nidq-timezone`** now also reads the eye file's stamp against the NI's `fileCreateTime` (amendment 85).
- The clock's choice rules are unchanged (`CHOICE_RULES` is still `"3b-3"`). 3b-1's purge, `--nidq-timezone` and
  backfill notes, and 3b-3's rebuild, stand.

## Told to

Nobody yet. At the merge:
- **wl.works:** the entry "the NI keeps time" in `docs/pending-wl-works-amendments.md` gains two lines. Under the NI
  the eye tracker may be placed by its analog copy, so such a session can be tier N; a listed rejected segment's
  `reason` may be `no_analog_match`; and the NWB file's `system_clocks.time_source` may read `analog`, both plain
  strings. The four NI texts and `time_source`'s text were reworded, the description's `notes` say how the eye was
  placed, and the daily report gains the section "Eye placed by its analog copy (24 h)".

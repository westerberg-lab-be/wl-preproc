# What wl.works' plan 20b-1b can send, and what both listings now carry

wl.works builds canonical NWB requests from `GET /sessions` and pages through `GET /sessions` and `GET /nwb`. Four
things stood in its way: a request could not mark a run bad or give the animal's nickname, listed runs did not carry
the task's hash, both listings returned everything after `since` in one response, and nothing kept a run, block or
file that ends before it starts from making `GET /sessions` answer 500 for every session. This piece closes those,
and three more asks that came in while it was built: a bound on the idempotency key and a retry that survives an
added field, the wall time of t = 0, and the rig as an actor. wl.works pins at one merge of ours.
- **Spec:** `docs/superpowers/specs/2026-11-24-wl-works-requests-design.md` (`671ee7f`, approved 2026-11-24).
- **Plan:** `docs/superpowers/plans/2026-11-24-wl-works-requests.md` (`cf2457d`). Tasks 9, 10 and 11 were added during
  the build and are appended to it in the commit of the records.
- **Branch:** `feat/wl-works-requests`, from the plan, from `main` at `ec4ab12`.
- **The ledger:** `.superpowers/sdd/2026-11-24-wl-works-requests/progress.md` holds the full wording of every
  ruling below. The executed order was Tasks 1-7, 9, 10, 11, 8 (P6): the records last, so they describe everything.

## What was built

By task, each with its commits.
- **Task 1, the request's fields** (`9224793`). `RunEntry.marked_bad` and `bad_reason` (at most 1000 characters; a
  mark without a reason, or a reason without a mark, is a 422 naming the run), `SubjectDetails.nickname` (1 to 64
  characters), `experimenter` at most 64 characters (empty allowed). `job_request.json` regenerated.
- **Task 2, the nickname** (`74bc566`, `23a3706`). Stored in `Subject.subject_nickname` when sent; written into the
  file's `subject.description` and the description's `subject.nickname`. A nickname equal to the subject's id (the
  landing stub's value) is not stated. `nwb/correct.py` keeps the nickname when it rebuilds the description's
  subject, so a correction of species, sex or birth date does not drop it.
- **Task 3, bad runs in the file** (`497c02d`). The runs table gains `marked_bad` and `bad_reason`; each run in the
  description gains the same two. They are read from the file's own request, so a file is reproducible from it.
- **Task 4, the task hash** (`cdb1e10`). `ListedRun.task_hash`, stored in a new `core.RunRecord.task_hash` column by
  the event stage, from the rule the file's `task_hash` column uses.
- **Task 5, ordered times in the listing** (`0381877`). `listing/order.py` holds the one rule and the messages. A
  run, block or file that ends before it starts is left out of the entry and flagged `ends_before_start`;
  `ListedFlag.segment_id` names a file. `ListedSegment`, `ListedRun` and `ListedBlock` refuse an end before a start,
  as the backstop.
- **Task 6, ordered times in requests and files** (`d5c9ddf`). `accept()` leaves such a run out of the runs a
  canonical's window requires, and refuses naming one (a 422 with both times). The build leaves the run, block or
  file out and names it in the file's `notes`.
- **Task 7, paging** (`5e1b930`). `?since=&limit=` on both listings; the cursor is the latest `change_seq` on the
  page. Only the page is built. **`GET /nwb`'s order changed**: by each file's latest `change_seq`, where it was by
  activation key.
- **Task 9, request bounds and retries across versions** (`0ef5001`). `idempotency_key` is 1 to 64 characters (a
  422). A retry's key-reuse comparison reads the stored payload through today's `JobRequest` first, so a field added
  with a default since is not a difference; a stored payload the current model refuses is compared as stored, so a
  field *removed* since storing still makes an honest retry a 409. `schema/request.py` imports no contract; the
  caller passes `read_stored_payload`.
- **Task 10, t = 0's wall time** (`2d45c5a`, `8934473`). The new `timebase.ClockReference` is computed once per
  session by `Clock.wall_reference` and read by the listing and the build. `SessionEntry.t0_datetime` (naive UTC,
  microseconds) and `t0_source` (`barcode`, `rig_clock`, `nidq_meta`, `manifest`; null until stored). `wlpp nwb build`
  no longer takes `--nidq-timezone`; the re-time route (delete the row, run a daemon pass with the zone set) is in
  `docs/ops/lab-host-protocol.md`.
- **Task 11, the rig as an actor** (`d8d1c3b`, `f69cbe6`). `ListedTrainingRecords.pauses` counts a person's pauses
  (a row with no actor included), the new `system_pauses` the rig's; the "box or member" texts, the `ListedActor`
  docstring and its schema description say "box, member or system (the rig itself)".
- **Task 8, the records.** `docs/ops/lab-host-protocol.md` (the 404 and 422 rows, both listings' `limit`, `GET
  /nwb`'s order, the marks, the nickname, the bounds, the 422 for a reversed run, the retry across versions, and the
  qualified `t0_datetime` sentence), `docs/pending-wl-works-amendments.md` (one block, and the 2026-11-07 question
  closed), this handoff, the plan's Tasks 9-11, and `test_the_404_row_names_every_path_and_both_query_paths`.

## Rulings and choices

In the order made, each with what it costs if wrong.
- **P1, pre-flight:** `listing/order.py`'s message functions are `run_message`, `block_message` and `segment_message`,
  each `(..., *, where)` with `where` one of `"listing"` and `"file"`. Why: the plan names `run_message` and "the
  same for blocks and segments". Cost if wrong: a rename.
- **P2:** Task 1's HTTP 422 test is written with Step 1's tests, so it fails before the validator exists. Cost if
  wrong: none.
- **P3:** the export command is `.venv/bin/wlpp schemas export --out docs/schemas`; the plan's text was wrong.
  Cost if wrong: none.
- **P4, Task 9:** a bound on the idempotency key and a retry that survives an added field (wl.works found it; every
  field added with a default reintroduces it). Cost if wrong: one small task.
- **P5, Task 10:** t = 0's wall time stored once and listed, with `t0_source` so wl.works can tell the exact case
  from the plus-or-minus 60 s fallback. Cost if wrong: one more field.
- **P6:** execution order 1-7, 9, 10, 11, then 8. Cost if wrong: none.
- **P7, Task 11:** `system_pauses` folded into this merge, though the option text said "a later merge": wl.works
  pins once, and the rows do not exist until wl-xcon's build E merges. Cost if wrong: one field moved to a later
  pin.
- **P8:** `0ef5001`'s trailer names Claude Sonnet 5.5, the implementer's own harness attribution, where the other
  commits name Opus 5.5; left as is. Cost if wrong: one inconsistent trailer.
- **P9, Task 10's review:** `--nidq-timezone` removed from `wlpp nwb build` (kept where the daemon pass that stores
  `ClockReference` takes it), since an option that does nothing misleads. Cost if wrong: an operator script passing
  it to `nwb build` fails, with argparse's clear message.
- **Regrade, Task 11:** the `ListedActor` docstring and its schema description joined the fix round: wl.works reads
  the schema's descriptions. Cost if wrong: a docstring edit.
- **My calls in the spec:** no storage of the marks beyond the stored request; `bad_reason`'s bound of 1000 and the
  experimenter's of 64; the nickname in `Subject.description`; no correction of a built file's nickname or marks in
  place; `core.RunRecord.task_hash`. The spec marks each *(my call)*; the bounds are announced in the amendments block.

### The requester's choices

- **2026-11-21, the nickname** is also carried into the NWB subject, over wl.works' recommendation. The marks carry
  no `marked_by` anywhere: wl.works withdrew it, because the name exception covers `metadata.experimenter` only. A
  tier-D refusal stays final; a refused canonical is replaced through `POST /jobs`.
- **2026-11-21, automatic reclamation** is not in this piece.
- **2026-11-24, ordered times:** a run, block or file that ends before it starts is dropped and flagged, and the rest
  of the session is listed ("bad parts dropped"), over listing the session with nothing to request. The requester
  chose this knowing that a file could then miss part of a session.
- **2026-11-24, execution:** subagent-driven.
- **2026-11-24, `t0_datetime`** is added to the listing in this piece (wl.works' ask: place a person's
  minute-level move times between runs without the 60 s of `session_datetime`).
- **2026-11-24, wl-xcon's actor** `{"kind": "system", "name": "display"}` (build E) is accepted as written, and the
  rig's pauses are counted separately in the listing.
- **2026-11-24, the moved-probe join is the next piece**, right after this one and before XC-513. The problem:
  `ephys.link_insertions` counts a serial across the session's whole `InsertionReport`, so a probe advanced mid-session
  (one request per montage, the same serial, its own `insertion_number`) joins neither insertion, and every file's
  area reads `unknown`. The direction: join each SpikeGLX file to the insertion reported by the request of the
  montage covering the file's time. The requester's requirement, verbatim: "it is important to remember that with
  probes like neuropixels, it is not just when the probe moves, but also which channels are selected. so we
  should be careful to recognize montages. and when there is partial montage overlap (e.g., half the channels are
  kept at the same depth, but the other half are changed between runs)". It is to be brainstormed with that at the
  front: a montage is position and channel selection, and partial overlap between montages is the case to decide
  (what unit identity and area mean for the kept half). `~/.cache/wl-preproc-scratch/2026-11-07-xcon-asks/RESUME.md`
  holds it.

## The task reviews

Every task had its own review. Counts are failures of review, not of tests.
- **Task 1** (Sonnet): approved, 4 Minor.
- **Task 2** (Sonnet): one Important, that `correct.py` rebuilt the description's subject without the nickname.
  Fixed in `23a3706`; clean after one round.
- **Task 3** (Sonnet): approved, 4 Minor.
- **Task 4** (Sonnet): approved, 3 Minor. No migration for an existing `core.RunRecord`, by design.
- **Task 5** (Sonnet): approved, 5 Minor.
- **Task 6** (Sonnet): approved, 5 Minor. The controller closed its null-stop-time concern: `core.Run.run_stop_time`,
  `BlockTime.stop_s` and `Segment.end_s` are all non-null doubles.
- **Task 7** (Sonnet): approved, 6 Minor. The cursor is sound: `change_seq` is auto_increment and unique.
- **Task 9** (Sonnet): approved, 5 Minor.
- **Task 10** (Opus): one Important, that `wlpp nwb build --nidq-timezone` did nothing. Settled by P9 and fixed in
  `8934473`; clean after one round.
- **Task 11** (Sonnet): one Important, no database-backed test of `system_pauses`. Fixed in `f69cbe6`, with the
  `ListedActor` text (the regrade above); clean after one round.

### For the whole-branch review

Named checks the task reviews carried forward:
- **Task 7:** `handler.py::_get_since` runs `int(limit)` outside the `try`, so a `limit` of more than 4300 digits
  raises `ValueError` (Python's int-string limit) and drops the connection instead of answering 422. `since`'s
  `int()` was inside the `try` before. A regression, though no honest client sends one.
- **Task 5:** a NaN start or end passes `ends_before_start` and the validators (`end < start` is false for NaN). Not
  a regression.
- **Task 6:** `_span_notes` (`gather.py`) still takes the minimum and maximum over a reversed SpikeGLX segment, so
  under the NI with a covered span, an ohdpi segment and no copy, a note can use a left-out file's times.
- **Task 6:** `tests/responder/test_http.py`'s two 5 s timeouts fail at the base too under load, and break the shared
  database connection for later tests. Run the merge suite when the machine is quiet.

### The whole-branch review

The final review (Opus, `ec4ab12..29adc4f`) found 0 Critical, 1 Important and 6 Minor. It closed the NaN check
as unreachable: requests refuse NaN (`allow_inf_nan=False`), and MySQL refuses NaN in the non-null double columns
the times are read from.
- **Fixed in one wave** (`afdaac7`; a scoped re-review found all four addressed and nothing broken):
  - **I1:** a `limit`, or a `since`, of more than 4300 digits is now a 422, not a dropped connection.
  - **M2:** `_span_notes` leaves reversed SpikeGLX segments out, so it keeps the one rule for what ends before it
    starts.
  - **M4:** the re-time route in `docs/ops/lab-host-protocol.md` covers a freed session (rehydrate it first).
  - **M5:** a nickname of only white space is a 422, as a `bad_reason` of only white space is.
- **Ruled to stand, M6:** a `bad_reason` of `""` on an unmarked run is a 422. The protocol and the amendments block
  say so, and wl.works is told to send null. Loosening it is a contract change wl.works would re-pin.
- **Deferred:** M3 and M7, below.

## Deferred minors

The ledger holds the full wording of each. The final review's fix wave closed three earlier ones: Task 7's
long `limit` (I1), Task 6's `_span_notes` (M2), and Task 1's white-space nickname (M5).
- **Final review:** M3, `_timebase` leaves reversed SpikeGLX segments out, but `provenance.n_segments` still counts
  them (its notes say why); M7, long lines (`entry.py:255`, the `ListedTrainingRecords` docstring).
- **Task 8:** the protocol's "timebase/clock_reference's reference_datetime" (~:702) uses the NWB-side name, where
  the table's column is `reference_time`; this handoff's "Four things" counts the marks and the nickname as one;
  the amendments keep a stale "Nothing is added until it does" beside the closure.
- **Task 1:** `_post_job` re-imports `json` and duplicates `test_http.py`'s server start-up; `match="run_number|run 7"`
  is a redundant alternation; `marked_bad=False` with `bad_reason=""` is refused, untested (a white-space
  nickname is now refused, M5).
- **Task 2:** the `described_subject` docstring should say the nickname is deliberately absent; `session.py`'s
  `new_file` docstring omits it.
- **Task 3:** no direct test of `_request_marks` on a payload lacking `metadata`, the mark keys or a run;
  `MARK_FIELDS` is defined after `RUN_COLUMNS` and left out of its comprehension; `marked_bad` is coerced twice.
- **Task 4:** `test_sessions_http` asserts `"sha256:synthetic"` without a comment.
- **Task 5:** `entry.probes` still lists a serial seen only in a dropped file; no test of a reversed block inside a
  reversed run.
- **Task 6:** `_order_notes` defines `in_window` twice; a block that does not end before it starts, inside a run
  that does, gets `_outside_runs_notes`' "RUN_START was lost" wording; a dead branch for a derivative naming a
  reversed run.
- **Task 7:** `nwb.py:524-526`'s `rows[moment]` raises `KeyError` for an `NwbChange` row with no `NwbFile` (likely
  unreachable); the paging logic is repeated in `sessions.py` and `nwb.py`; no between-pages test for `/nwb`.
- **Task 9:** the raw-fallback branch (a stored payload the current model refuses) is untested; only the canonical
  submit path is exercised; no pin that re-dumping a fresh payload is the identity.
- **Task 10:** a `ClockReference` make that errors parks the job, and readiness then says only "not stored yet"; no
  stage-level test of `nidq_meta` with the zone set.
- **Task 11:** the `by_kind` match relies on MySQL's case-insensitive collation (untested); the database test
  cannot tell a `"member"` filter from `"system"`, since it has one row of each.

## Carried to later parts

- **The moved-probe join** (the requester's next piece; see the choice above, with their requirement verbatim). It
  needs a design before a plan: montage as position and channel selection, and partial overlap.
- **Automatic reclamation** (the requester, 2026-11-21: not in this piece; it stays in the queue).
- **Then XC-513 and XC-543's reader, and part 2's bridges**, which this piece was moved ahead of.
- **A re-time that reaches built files.** A session re-timed after files were built leaves those files with the old
  `t0`; only a file built after the re-time carries the new one. Nothing corrects a built file's marks or nickname in place either.

## The suite

At `afdaac7`, with every reference set (NSLR, BMD and Boost, Andersson, the OpenIris recording), run one
interpreter after the other:
- **Python 3.11:** 3520 passed, 2 skipped, 1 deselected, 1 xfailed, in 1:03:48. The skips: U'n'Eye (numba cannot
  load OpenMP here) and the `.bhv2` sample (`WLPP_BHV2_SAMPLE` unset).
- **Python 3.13:** 3520 passed, 3 skipped, 1 xfailed, in 1:02:22. The skips: two Kilosort tests (not installed
  in that venv) and the `.bhv2` sample.
- The two `test_http.py` timeouts seen under load during the build did not recur.

## Deploy

- **Development databases are rebuilt, never migrated.** New: `core.RunRecord.task_hash` and the table
  `timebase.ClockReference`.
- **Every listed session's digest changes once**, so every session is listed once more: each flag carries
  `segment_id`, each run `task_hash`, each session `t0_datetime`, `t0_source` and `system_pauses`.
- **A session has no `ClockReference` row until a daemon pass stores it.** Until then it lists `t0_datetime` and
  `t0_source` as null, and its NWB builds wait ("waiting on ClockReference"). No session that was buildable before
  waits longer, since a stored row needs only what the activation already needs.
- **`wlpp nwb build --nidq-timezone` is gone.** The daemon's `--nidq-timezone` stays, and sets the zone a session's
  `ClockReference` is stored with. A zone set later changes no stored session; the re-time route is in
  `docs/ops/lab-host-protocol.md`.
- **`GET /nwb`'s order changed** (by each file's latest `change_seq`). An unpaged call returns the same files and
  cursor.
- **Files built before this stay as they are.** None carries a mark or a nickname unless a later request does.
- **`wl.yaml`:** no dependency changed.

## Told to

Nobody yet. At the merge:
- **wl.works:** the block "Added by the wl.works-facing piece (2026-11-24)" in `docs/pending-wl-works-amendments.md`
  lists every change. Three schema files change, and its plan 20b-1b pins at the merge. `limit` may be sent only
  after it. The order of `GET /nwb` changed. An unmarked run's reason is null, not `""`. The listing's `plan` and
  `end_reason` question is closed on its own answer of 2026-11-21.
- **wl-xcon:** its build E's actor `{"kind": "system", "name": "display"}` is accepted as written, and the rig's
  pauses are counted apart in the listing.

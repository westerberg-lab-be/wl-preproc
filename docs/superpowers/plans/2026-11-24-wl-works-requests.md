# The wl.works-facing piece: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** wl.works' 20b-1b can send bad-run marks, the animal's nickname and the experimenter. It can page
through both listings, and read each listed run's task hash. No run, block or file that ends before it starts
can break a listing, a request or a file.

**Architecture:** The request contracts (`contracts/protocol.py`) gain fields and validators. `responder/jobs.py`
stores the nickname, and leaves reversed runs out of a canonical's window. The NWB builder (`nwb/gather.py`,
`nwb/run_record.py`, `nwb/session.py`, `contracts/nwb_description.py`) reads a file's marks from its own stored
request and leaves reversed items out. The listing (`listing/entry.py`) drops and flags reversed items. Both
listings page (`responder/handler.py`, `responder/sessions.py`, `responder/nwb.py`). One helper module names what
ends before it starts, for all three callers.

**Tech Stack:** Python 3.11/3.13, pydantic 2, DataJoint 2 on MySQL (testcontainer), pynwb.

**Spec:** `docs/superpowers/specs/2026-11-24-wl-works-requests-design.md` (approved 2026-11-24).

## Global Constraints

- Every contract model stays `extra="forbid"`. A field added within a version has a default, so older payloads
  read unchanged. `nwb_description` stays at `SCHEMA_VERSION = 3`.
- `bad_reason`: at most 1000 characters. `nickname`: 1 to 64 characters. `experimenter`: at most 64 characters,
  empty allowed.
- Flag code `"ends_before_start"`. `ListedFlag.segment_id: str | None = None`.
- `limit`: one positive integer, no upper bound, absent means everything. Cursor: the latest `change_seq` on the
  page, or `since` (or 0) on an empty page.
- Equal start and end is never "ends before start".
- Development databases are rebuilt, never migrated.
- Regenerate `docs/schemas/*.json` with `wlpp schemas export docs/schemas` in the task that changes a contract.
- Never commit the OpenIris reference recording, the Andersson dataset, or NSLR/BMD reference code or data.
  Leave the root `wl-preproc` symlink out. Stage files by name. Never commit while a test run is going on this
  checkout.
- Floats that go through trig are compared to a tolerance.
- The tests run with `.venv/bin/python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800 <paths>`.
  The database-backed tests need Docker (OrbStack).
- Commit trailer:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01HzkMmyxfPZxnrtSqNEictD`

## Review Focus

1. **A blank reason.** `bad_reason` of only whitespace, with `marked_bad: true`, is not a reason: a 422.
   (Task 1.)
2. **An earlier mark.** A run marked in an earlier request and unmarked in the replacement reads `false` and ''
   in the replacement's file. Each file reads its own request. (Task 3.)
3. **Malformed paging.** `limit=0`, `limit=-1`, `limit=1.5`, `limit=` and a repeated `limit` are 422s. So is
   `limit` with an unknown key. `limit=01` is 1. (Task 7.)
4. **A session that changes mid-paging.** A session listed on page 1 and then changed again appears on a later
   page. No session is lost between pages. (Task 7.)
5. **A window with only reversed runs.** A canonical whose montage window holds only reversed runs is a 422
   naming them. It is not an empty file. (Task 6.)

---

### Task 1: The request contracts

**Files:**
- Modify: `wl_preproc/contracts/protocol.py` (`SubjectDetails`, `RunEntry`, `MetadataBundle`)
- Modify: `docs/schemas/job_request.json` (regenerated)
- Test: `tests/contracts/test_protocol.py`, `tests/responder/test_jobs.py`

**Interfaces:**
- Produces:
  - `RunEntry.marked_bad: bool = False`
  - `RunEntry.bad_reason: Annotated[str, Field(max_length=1000)] | None = None`
  - `SubjectDetails.nickname: Annotated[str, Field(min_length=1, max_length=64)] | None = None`
  - `MetadataBundle.experimenter: Annotated[str, Field(max_length=64)]`

- [ ] **Step 1: Write the failing tests** in `tests/contracts/test_protocol.py`:
  - `test_a_marked_run_needs_a_reason`: `marked_bad=True` with `bad_reason` None, '' or `"   "` raises
    `ValidationError`, and the message names `run_number`.
  - `test_a_reason_needs_a_mark`: `bad_reason="x"` with `marked_bad=False` raises `ValidationError`.
  - `test_a_marked_run_with_a_reason_is_accepted`: `marked_bad=True, bad_reason="eye lost"` round-trips.
    `bad_reason` of 1000 characters is accepted, and of 1001 refused.
  - `test_nickname_is_one_to_sixty_four_characters`: '' and 65 characters are refused; "Pico" and 64 characters
    are accepted.
  - `test_experimenter_is_at_most_sixty_four_characters`: 64 accepted, 65 refused, '' accepted.
  - `test_a_request_without_the_new_fields_reads_as_before`: today's `_request` payload in the module validates,
    with `marked_bad` False and `nickname` None.
- [ ] **Step 2:** Run them. Expected: FAIL (unknown fields, or no refusal).
- [ ] **Step 3: Implement** the fields above, plus a `model_validator(mode="after")` on `RunEntry`:
  - a mark needs `bad_reason` that is non-empty after `.strip()`;
  - a reason needs the mark;
  - each refusal says `f"run {run_number}: ..."`.
  - Raise `PydanticCustomError`, not `ValueError`. Read the comment at `MetadataBundle._blocks_are_retired` for
    why.
- [ ] **Step 4: Write the failing HTTP test** in `tests/responder/test_jobs.py`,
  `test_a_marked_run_without_a_reason_is_a_422`: `POST /jobs` with such a run answers 422, and its body names the
  run. Use the module's existing HTTP helper. Then `test_a_replacement_of_a_refused_canonical_is_accepted`:
  - land a session and accept a canonical;
  - give its file the status `refused` in `nwb` (as `tests/schema/test_nwb_build.py` builds a refused file, or by
    inserting the row);
  - send the replacement through `POST /jobs`, with `selection.role == "canonical"` and
    `selection.supersedes_activation_id` set to the refused one's id (`responder/jobs.py::_lifecycle_role`);
  - assert 200, and a new activation whose `supersedes` is the old id
    (`tests/schema/test_request.py::test_a_replacement_supersedes_the_current_canonical_at_the_next_id` shows the
    rule).
  - If `accept()` refuses it, stop and report: the spec says this path exists.
- [ ] **Step 5:** Run them. The 422 test is expected to FAIL before Step 3 and pass now. The replacement test is
  expected to PASS. It pins existing behaviour; write down its result.
- [ ] **Step 6:** Run `.venv/bin/wlpp schemas export docs/schemas`. Then run `git diff --stat docs/schemas`.
  Expected: only `job_request.json` changes.
- [ ] **Step 7:** Run `tests/contracts tests/responder/test_jobs.py tests/cli/test_schemas_export.py`. Expected:
  PASS.
- [ ] **Step 8: Commit** `feat(contracts): a request may mark a run bad, give the animal's nickname, and an
  experimenter of at most 64 characters`.

### Task 2: The nickname, stored and in the file

**Files:**
- Modify: `wl_preproc/responder/jobs.py::_record_subject_details`
- Modify: `wl_preproc/nwb/gather.py::_subject`
- Modify: `wl_preproc/nwb/session.py` (the `Subject` description)
- Modify: `wl_preproc/contracts/nwb_description.py::SubjectInfo`
- Modify: `wl_preproc/nwb/describe.py`
- Modify: `docs/schemas/nwb_description.json`
- Test: `tests/responder/test_jobs.py`, `tests/nwb/test_describe.py`, `tests/schema/test_nwb_build.py`,
  `tests/schema/test_nwb_correct.py`

**Interfaces:**
- Consumes: Task 1's `SubjectDetails.nickname`.
- Produces:
  - `_subject(subject_id)` gains `"nickname": str | None`. It is None when `subject_nickname` is null or equals
    `subject_id`, the landing stub.
  - `SubjectInfo.nickname: str | None = None`.

- [ ] **Step 1: Write the failing tests:**
  - `test_jobs.py::test_a_nickname_is_stored_and_kept_when_absent`: a request with `nickname="Pico"` sets
    `Subject.subject_nickname` to "Pico", and a later request without it leaves "Pico".
  - `test_nwb_build.py::test_the_file_states_the_animals_nickname`:
    - the built file's `subject.description` is exactly "Nickname Pico, as wl.works' record gives it. As
      wl.works' job request stated it (design spec section 9).";
    - its description's `subject.nickname == "Pico"`.
  - `test_nwb_build.py::test_a_stub_nickname_is_not_stated`: with no nickname sent, the description text is
    exactly "As wl.works' job request stated it (design spec section 9)." and `subject.nickname` is None. So is a
    nickname sent equal to the subject id.
  - `test_describe.py::test_a_description_without_a_nickname_reads`: a stored description dict without
    `subject.nickname` validates, with None.
  - `test_nwb_correct.py::test_a_changed_nickname_marks_no_file_stale`: after a build, setting a new nickname leaves
    `correct.stale_files()` empty.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** `gather.described_subject` stays without the nickname (spec §4.2).
- [ ] **Step 4:** Run the five tests, plus `tests/nwb tests/schema/test_nwb_build.py tests/schema/test_nwb_correct.py
  tests/responder`. Expected:
  PASS.
- [ ] **Step 5:** Regenerate the schemas. Expected: only `nwb_description.json` changes.
- [ ] **Step 6: Commit** `feat(nwb): the animal's nickname, as wl.works' record gives it, in the file's subject`.

### Task 3: Bad-run marks in the file

**Files:**
- Modify: `wl_preproc/nwb/gather.py` (a reader of the activation's request; the runs it gathers)
- Modify: `wl_preproc/nwb/run_record.py::RUN_COLUMNS`
- Modify: `wl_preproc/contracts/nwb_description.py::Run`
- Modify: `wl_preproc/nwb/describe.py`
- Modify: `docs/schemas/nwb_description.json`
- Test: `tests/schema/test_nwb_build.py`, `tests/nwb/test_run_record.py`, `tests/nwb/test_describe.py`

**Interfaces:**
- Consumes: Task 1's `RunEntry` fields.
- Produces:
  - `gather._request_marks(activation_key: dict) -> dict[int, tuple[bool, str]]`. It reads
    `request.Request.payload` via the activation's `request_key`, and returns `{run_number: (marked_bad,
    bad_reason or "")}`. A run absent from `metadata.runs` is `(False, "")`.
  - Each gathered run dict gains `marked_bad: bool` and `bad_reason: str`.
  - `Run.marked_bad: bool = False` and `Run.bad_reason: str | None = None` in the description (None where not
    marked).

- [ ] **Step 1: Write the failing tests:**
  - `test_run_record.py::test_the_runs_table_has_the_marks`: `RUN_COLUMNS` holds `marked_bad` with description
    "Whether wl.works' record marks the run bad for behaviour, as the request stated it.", and `bad_reason` with
    "Why, as wl.works' record gives it ('' if not marked)."
  - `test_nwb_build.py::test_a_marked_run_is_marked_in_its_file`: a canonical marking run 1 "eye lost" builds a
    file whose runs table reads `marked_bad` True and `bad_reason` "eye lost" for run 1, and False and '' for the
    rest. Its description agrees, with `bad_reason` None for the rest.
  - `test_nwb_build.py::test_each_file_reads_its_own_requests_marks` (Review Focus 2): the canonical marks run 1;
    the replacement, sent unmarked, builds a file reading False and ''. The first file is unchanged.
  - `test_describe.py::test_a_description_without_marks_reads`: a run dict without the two fields validates with
    the defaults.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** The writer reads the two fields from each run dict, like the other `RUN_COLUMNS`.
  `marked_bad` is written as a boolean column.
- [ ] **Step 4:** Run the four tests, plus `tests/nwb tests/schema/test_nwb_build.py`. Expected: PASS.
- [ ] **Step 5:** Regenerate the schemas. Expected: only `nwb_description.json` changes.
- [ ] **Step 6: Commit** `feat(nwb): a run wl.works marks bad is marked in the file, from that file's own
  request`.

### Task 4: The task hash on listed runs

**Files:**
- Modify: `wl_preproc/schema/core.py::RunRecord` (`task_hash = null : varchar(255)`)
- Modify: `wl_preproc/schema/events.py` (the `core.RunRecord` rows, about :634-646)
- Modify: `wl_preproc/listing/entry.py` (`SessionFacts.records`, the run dicts)
- Modify: `wl_preproc/contracts/protocol.py::ListedRun`
- Modify: `docs/schemas/session_listing.json`
- Test: `tests/schema/test_events.py`, `tests/listing/test_entry.py`, `tests/responder/test_sessions_http.py`

**Interfaces:**
- Consumes: `events/rigruns.py::RigRun.task_hash`, already set by `_content_hash`, which is the rule
  `nwb/run_record.py` uses. Do not write a second rule. The file keeps reading it from `RigRun`, and the listing
  reads the stored column.
- Produces: `ListedRun.task_hash: str | None = None`.

- [ ] **Step 1: Write the failing tests:**
  - `test_events.py::test_the_run_record_keeps_the_task_hash`: a session whose `runs.jsonl` start row has
    `versions.task` `"sha256:" + 64 hex` stores it in `core.RunRecord.task_hash`. One naming a path stores null.
  - `test_entry.py::test_a_listed_run_carries_its_task_hash`: facts with a record holding `task_hash` list it, and
    without one list null.
  - `test_sessions_http.py::test_get_sessions_lists_the_task_hash`: one landed session, through HTTP.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** `gather_all` reads the column with the other `RunRecord` fields.
- [ ] **Step 4:** Run `tests/schema/test_events.py tests/listing tests/responder/test_sessions_http.py
  tests/nwb/test_run_record.py`. Expected: PASS.
- [ ] **Step 5:** Regenerate the schemas. Expected: only `session_listing.json` changes.
- [ ] **Step 6: Commit** `feat(listing): each listed run carries its task's content hash`.

### Task 5: Ordered times in the listing

**Files:**
- Create: `wl_preproc/listing/order.py`
- Modify: `wl_preproc/contracts/protocol.py` (`ListedFlagCode`, `ListedFlag`, and the `ListedSegment`, `ListedRun`
  and `ListedBlock` validators)
- Modify: `wl_preproc/listing/entry.py::build_entry`, `_flag`
- Modify: `docs/schemas/session_listing.json`
- Test: `tests/listing/test_order.py` (create), `tests/listing/test_entry.py`,
  `tests/responder/test_sessions_http.py`, `tests/contracts/test_protocol.py`

**Interfaces:**
- Produces, in `wl_preproc/listing/order.py`:
  - `ends_before_start(start_s: float, end_s: float) -> bool`, which is `end_s < start_s`.
  - `run_message(run_number: int, start_s: float, end_s: float) -> str`, and the same for blocks and segments.
  - The exact text: `f"run {n} ends at {end_s:.3f} s, before its start at {start_s:.3f} s: left out of this
    listing"`.
    - A block reads `block {b} of run {n}`, or `block {b}` with no run.
    - A file reads `file {segment_id}`.
  - Task 6 uses the same `ends_before_start` and the same item wording, ending in "left out of this file"
    instead. Give each message function a `where: str` parameter, `"listing"` or `"file"`.
- Produces:
  - `_flag(..., segment_id: str | None = None)`.
  - `ListedFlag.segment_id: str | None = None`.
  - `"ends_before_start"` in `ListedFlagCode`.

- [ ] **Step 1: Write the failing tests:**
  - `test_order.py::test_equal_times_do_not_end_before_start`: `ends_before_start(5.0, 5.0)` is False, and
    `(5.0, 4.999)` is True.
  - `test_order.py::test_the_messages`: the three texts, exactly, for both `where` values.
  - `test_protocol.py::test_listed_items_refuse_an_end_before_the_start`: each of `ListedSegment`, `ListedRun` and
    `ListedBlock` refuses `end_s < start_s` and accepts equal times.
  - `test_entry.py::test_a_reversed_file_is_left_out_and_flagged`: the segment is not in `segments` nor in any
    run's `segments`, and one flag has `code="ends_before_start"`, `segment_id` set, and the run and block fields
    None.
  - `test_entry.py::test_a_reversed_run_is_left_out_and_flagged`: the run is absent and flagged with
    `run_number`. A block starting between its stop and its start is flagged `block_outside_runs`.
  - `test_entry.py::test_a_reversed_block_is_left_out_and_flagged`: it is absent from its run's `blocks` and
    flagged with both numbers. The run's other blocks are numbered `block_in_run` from 1 without it, as the file's
    `_blocks` counts only the blocks it holds.
  - `test_entry.py::test_a_run_whose_only_block_was_left_out_has_no_run_without_block_flag`.
  - `test_sessions_http.py::test_one_reversed_session_does_not_break_the_page`: two landed sessions, one with a
    reversed run, block and file written into `core` after landing. `GET /sessions` answers 200 and lists both:
    the bad one with its three flags, the other whole.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `build_entry` checks the facts with `ends_before_start` before building any listed item.
  - The validators raise `PydanticCustomError`.
  - Edit the `ListedFlag` docstring to say that `ends_before_start` is the one flag that means something was
    withheld.
- [ ] **Step 4:** Run `tests/listing tests/contracts tests/responder/test_sessions_http.py tests/schema/test_session_listing.py`.
  Expected: PASS. The daemon's listing stage hashes `build_entry`'s output, so its tests must stay green
  unchanged.
- [ ] **Step 5:** Regenerate the schemas. Expected: only `session_listing.json` changes.
- [ ] **Step 6: Commit** `feat(listing): a run, block or file that ends before it starts is left out and
  flagged, and breaks no page`.

### Task 6: Ordered times in requests and files

**Files:**
- Modify: `wl_preproc/responder/jobs.py::_check_runs`
- Modify: `wl_preproc/nwb/gather.py` (`_blocks`, `_timebase`'s segments, a new notes list)
- Modify: `wl_preproc/nwb/describe.py` (the notes)
- Test: `tests/responder/test_jobs.py`, `tests/schema/test_nwb_build.py`

**Interfaces:**
- Consumes: Task 5's `order.ends_before_start` and message functions (`where="file"`).
- Produces: `Gathered.order_notes: list[str]`, added to the description's `notes` after `span_notes`.

- [ ] **Step 1: Write the failing tests:**
  - `test_jobs.py::test_a_canonical_need_not_assert_a_reversed_run`: a session whose run 2 is reversed. A
    canonical asserting the window's other runs is accepted, and its `request.ActivationRun` rows leave run 2 out.
  - `test_jobs.py::test_naming_a_reversed_run_is_a_422`: asserting run 2 in `metadata.runs`, or naming it in a
    derivative's `run_numbers`, is a 422 whose message names run 2 and both its times.
  - `test_jobs.py::test_a_window_of_only_reversed_runs_is_a_422` (Review Focus 5): the message names the runs.
  - `test_nwb_build.py::test_the_file_leaves_out_what_ends_before_it_starts`: with a reversed block in run 1 and a
    reversed SpikeGLX segment:
    - the file holds no run 2, no such block (in neither the blocks table nor the trials' block ids) and no
      such segment row in `timebase/segments`;
    - its description's notes hold the three "left out of this file" texts.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - In `_check_runs`, the reversed runs leave `window` before the missing-run check.
  - A reversed run in `by_number`, or in `run_numbers`, raises `ValueError` with the Task 5 item wording plus
    "; it cannot be requested".
- [ ] **Step 4:** Run `tests/responder tests/schema/test_nwb_build.py tests/nwb`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: a run, block or file that ends before it starts is left out of requests and
  files, and named`.

### Task 7: Paging both listings

**Files:**
- Modify: `wl_preproc/responder/handler.py::_get_since`
- Modify: `wl_preproc/responder/sessions.py::list_sessions`
- Modify: `wl_preproc/responder/nwb.py::list_files`
- Test: `tests/responder/test_http.py`, `tests/responder/test_sessions_http.py`, `tests/responder/test_nwb_http.py`

**Interfaces:**
- Produces:
  - `list_sessions(since: int | None, limit: int | None = None, prefix=...) -> dict`
  - `list_files(since: int | None, limit: int | None = None, prefix=...) -> dict`
  - The handler calls `list_fn(since, limit)`. Update every fake `list_fn` in the tests to take two
    arguments.

- [ ] **Step 1: Write the failing tests:**
  - `test_http.py::test_paging_parameters_are_checked` (Review Focus 3): 422 for `limit=0`, `limit=-1`,
    `limit=1.5`, `limit=`, `limit=1&limit=2` and `since=1&limit=1&x=1`. The 422 body reads "the only parameters
    are since, one non-negative integer, and limit, one positive integer". `limit=01` passes 1.
  - `test_sessions_http.py::test_sessions_page_by_limit`: five changed sessions and `limit=2` give pages of 2, 2
    and 1. Each cursor is the page's latest `change_seq`. The short page ends it. An empty page's cursor equals
    `since`.
  - `test_sessions_http.py::test_a_session_changed_between_pages_is_not_lost` (Review Focus 4): one listed on page
    1 that changes before page 2 appears on a later page.
  - `test_sessions_http.py::test_only_the_page_is_built`: with `entry.build_entry` wrapped to count calls,
    `limit=2` over five sessions builds two.
  - `test_nwb_http.py::test_files_page_by_limit`: the same over activations.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** Order and cursor are as the spec's §4.4 says. `gather_all` runs over the page's
  sessions only.
- [ ] **Step 4:** Run `tests/responder`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(responder): GET /sessions and GET /nwb page by limit`.

### Task 8: The records

**Files:**
- Modify: `docs/ops/lab-host-protocol.md`:
  - the 404 row at about :658: the paths include `/sessions`, and a query string is allowed on `/nwb` and
    `/sessions`;
  - the two endpoints' `?since=&limit=`;
  - `metadata.runs[].marked_bad` and `bad_reason`;
  - `subject_details.nickname`;
  - `experimenter`'s bound;
  - the `ends_before_start` flag.
- Modify: `docs/pending-wl-works-amendments.md`:
  - one new block, "Added by the wl.works-facing piece (2026-11-24)", naming each change of Tasks 1-7, what a
    vendored copy must take, and that `limit` may now be sent;
  - close the 2026-11-07 open question on listing runs' `plan` and `end_reason`, citing wl.works' answer of
    2026-11-21 that it is not needed.
- Create: `docs/handoffs/2026-11-24-wl-works-requests.md`, the handoff, with `FINAL_REVIEW` and `SUITE_RESULTS`
  placeholders, as `docs/handoffs/2026-11-21-eye-by-analog-copy.md` is laid out.
- Test: `tests/responder/test_protocol_document.py`

- [ ] **Step 1: Write the failing test**
  `test_protocol_document.py::test_the_404_row_names_every_path_and_both_query_paths`: the 404 row names `/sessions`,
  and says a query string is allowed on `/nwb` and `/sessions`.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Edit the documents.
- [ ] **Step 4:** Run `tests/responder/test_protocol_document.py`. Expected: PASS.
- [ ] **Step 5: Commit** `docs: the wl.works-facing piece's records`.

## Added during the build

Tasks 9, 10 and 11 were added after the plan was written, by the controller's rulings P4, P5 and P7 (the
ledger, `.superpowers/sdd/2026-11-24-wl-works-requests/progress.md`). **The executed order was 1-7, 9, 10, 11, 8**
(ruling P6): the records last, so they describe everything. The briefs are in the same directory, as `task-9-brief.md`
to `task-11-brief.md`; what follows is the delivered shape.

### Task 9: Request bounds, and retries across versions (ruling P4; `0ef5001`)

wl.works found that a retry of a request stored before a field was added is refused as key reuse: `accept()` stores
`request.model_dump(mode="json")`, defaults included, and the retried body dumps with the new field's default.
- `JobRequest.idempotency_key` is 1 to 64 characters (`request.Request.idempotency_key` is `varchar(64)`), a 422.
- `schema/request.py::_reject_key_reuse`, and `submit`, `submit_replacement` and `submit_derivative`, take an optional
  `read_stored_payload`, so the module imports no contract. `responder/jobs.py::_as_current_payload` re-reads the
  stored payload through `JobRequest` (`model_validate`, then `model_dump(mode="json")`) before comparing, and
  returns it as stored when the model refuses it. So a field *removed* since storing still makes an honest retry a 409.
- Files: `contracts/protocol.py`, `schema/request.py`, `responder/jobs.py`, `docs/schemas/job_request.json`.

### Task 10: t = 0's wall time, stored once and listed (ruling P5, the requester's choice of 2026-11-24; `2d45c5a`, `8934473`)

- The new `dj.Computed` `timebase.ClockReference` (keyed on `SessionClock` and `pipeline.event.BehaviorRecording`)
  stores `reference_time`, `reference_source` and `started_at_difference_s`, from the existing
  `Clock.wall_reference`, once per session. The NWB build reads it, and waits ("waiting on ClockReference") where
  it is not stored; the events stage's redo deletes it by cascade.
- `SessionEntry.t0_datetime` (naive UTC, microseconds) and `t0_source` (`barcode`, `rig_clock`, `nidq_meta`,
  `manifest`; null until stored), from the stored row. `session_listing.json` regenerated.
- After the task's review (ruling P9), `wlpp nwb build` no longer takes `--nidq-timezone`; the re-time route is
  written in `docs/ops/lab-host-protocol.md`.

### Task 11: The rig as an actor (ruling P7, the requester's choices of 2026-11-24; `d8d1c3b`, `f69cbe6`)

- `ListedTrainingRecords.pauses` counts pause rows whose `by_kind` is not `"system"` (null included); the new
  `system_pauses: int = 0` counts those whose `by_kind` is `"system"`. `listing/entry.py` gathers both.
- The texts that said "box or member" (`nwb/run_record.py`, `nwb/rig_record.py`, `schema/core.py`'s column
  comments, the `ListedActor` docstring and its schema description) say "box, member or system (the rig itself)".
- wl-xcon's build E's actor `{"kind": "system", "name": "display"}` is accepted as written. `session_listing.json`
  regenerated.

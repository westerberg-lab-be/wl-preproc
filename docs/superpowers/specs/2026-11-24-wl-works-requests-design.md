# The wl.works-facing piece: what 20b-1b sends, and what the listings guarantee

**Status:** design, written 2026-11-24. The requester moved this piece ahead of the XC-513 and XC-543 readers and
part 2's bridges that day, so that wl.works' plan 20b-1b ("confirm and send", building canonical requests to
`POST /jobs`) can send every field in its first version and pin at one merge of ours.

**Sources.** Each item below was agreed with wl.works (sessions wl-works-ea, wl-works-78, 2026-11-20 to 2026-11-24;
recorded in its Plan 20 §15, last at wl-works `43921219`). The requester's own decisions are marked *(requester,
date)*. A ruling that wl.works relayed and that the requester has not confirmed here is marked *(relayed)*. My
calls are marked *(my call)*.

## 1. Intent

wl.works builds canonical NWB requests from what `GET /sessions` lists, and pages through `GET /sessions` and
`GET /nwb` to keep its store current. Four things stand in its way today:

- A request cannot say that a run was bad for behaviour, or give the animal's nickname.
  - `MetadataBundle`, `RunEntry` and `SubjectDetails` refuse unknown fields (`extra="forbid"`), so either field is
    a 422.
- Listed runs do not carry the task's content hash.
  - wl.works' task pages key on it (wl-works `docs/superpowers/specs/2026-11-20-task-pages-design.md` §7.2, at
    `ac35847d`).
- Both listings return everything after `since` in one response.
- Nothing guarantees that a listed run, block or file ends at or after its start.
  - It holds by construction: `events/assemble.py` reads a time-ordered stream, and every clock fit has a positive
    rate.
  - But no validator enforces it. A violation would also make `GET /sessions` answer 500 for **every** session, on
    every retry. `responder/sessions.py` builds the whole page, and wl.works retries a 500.

## 2. Goals

1. **Bad runs.** `RunEntry` gains `marked_bad` and `bad_reason`. They are stored for the run, and written into
   the NWB file's runs table and the file's description. *(Agreed 2026-11-21. The final shape has no `marked_by`
   anywhere: wl.works withdrew it, because the requester's name exception covers `metadata.experimenter` only.)*
2. **The nickname.** `SubjectDetails` gains `nickname`. It is stored in element-animal's
   `Subject.subject_nickname`, and carried in the NWB file's subject and in the description's subject. *(requester,
   2026-11-21: from wl.works' name, sent with the job request. That it is also carried into the NWB subject was
   the requester's ruling over wl.works' recommendation.)*
3. **The task hash.** `ListedRun` gains `task_hash`. *(Agreed 2026-11-20.)*
4. **Paging.** `GET /sessions` and `GET /nwb` take `?since=<cursor>&limit=<n>`. *(The shape was agreed with
   wl.works 2026-11-21 and confirmed by it on the requester's word.)*
5. **Ordered times.** Any listed run, block or file that ends before it starts is dropped from the listing and
   flagged; the rest of the session is listed. *(The requester confirmed it 2026-11-24, choosing "bad parts
   dropped" over "listed with nothing to request".)*
6. **`metadata.experimenter` is at most 64 characters**, refused with a 422. Today a longer value fails at the
   database, because `request.Request.requested_by` is `varchar(64)`. *(my call; wl.works told 2026-11-24.)*
7. **Two records put right:**
   - `docs/ops/lab-host-protocol.md`'s 404 row omits `/sessions`, and says a query string is allowed on `/nwb`
     only.
   - `docs/pending-wl-works-amendments.md`'s open question of 2026-11-07, on listing runs' `plan` and
     `end_reason`, is closed: wl.works said 2026-11-21 that it is not needed.
8. **A test** that a replacement of a refused canonical is accepted. wl.works confirmed 2026-11-21 that this is
   the path for a refused canonical (and so for a tier-D refusal, which stays final: *requester, 2026-11-21*).
   The behaviour is believed to exist, but no test shows it.

## 3. Non-goals and anti-goals

- **No other change to how a file is built** than §4.5's: a run, block or file that ends before it starts is left
  out of the file, as it is out of the listing. The requester chose this knowing that a file could then miss part
  of the session (2026-11-24).
- **No correction of files already built when a run's mark changes.** A file states the marks its request
  carried. A later mark reaches a file through wl.works' replacement request, not through `nwb/correct.py`.
  *(my call: a mark is part of the request, like the run set; the subject's details are the precedent for
  correcting a built file in place, and they are the ELN's record of the animal, not of one request.)*
- **Automatic reclamation** *(requester, 2026-11-21)* is not in this piece; it stays in the queue.
- **Anti-goal:** a listing that one bad session can still bring down. After this piece, no single session's facts
  can make `GET /sessions` answer 500 because of their times.

## 4. The design

### 4.1 Bad runs (`RunEntry.marked_bad`, `RunEntry.bad_reason`)

- **Shape.** `marked_bad: bool = False` and `bad_reason: str | None = None`, at most 1000 characters *(my call on
  the bound; tell wl.works)*.
  - A mark without a reason, or a reason without the mark, is a 422 naming the run.
  - The reason is free text, carried as given. *(relayed: the requester ruled that a run's bad-for-behaviour mark
    follows wl.works' probe-mark rules, so a reason is always present with a mark. Enforcing both directions is my
    call.)*
- **Storage: none of its own.** The marks stay in the request as received: `request.Request.payload` already
  keeps it, as evidence. Nothing but the file reads them, so a table would only be written. *(my call, after the
  builder check.)*
  - Not `core.RunAssertion`: that table is insert-if-absent and refuses a changed id, whereas a mark changes
    between requests.
  - A later mark reaches a file only through a later request, and so a replacement. Each file states the marks
    its own request carried.
- **In the NWB file.** The runs table (`nwb/run_record.py::RUN_COLUMNS`) gains two columns:
  - `marked_bad`, "Whether wl.works' record marks the run bad for behaviour, as the request stated it.";
  - `bad_reason`, "Why, as wl.works' record gives it ('' if not marked)."
  - They are read from the file's own request (`request.Request.payload`, through the activation's
    `request_key`), so a file is reproducible from its request.
  - A run absent from the request's `metadata.runs` reads false and ''.
- **In the description.** Each run gains `marked_bad` and `bad_reason`, defaulted within version 3 (false and
  null), as every field added within a version is (`nwb/correct.py::as_described_now`).

### 4.2 The nickname (`SubjectDetails.nickname`)

- **Shape.** `nickname: str | None = None`, 1-64 characters. Longer is a 422; `Subject.subject_nickname` is
  `varchar(64)`.
- **Storage.** `_record_subject_details` writes it into `Subject.subject_nickname` when present. Absent, the
  stored nickname is unchanged.
  - The landing stub's `subject_nickname = manifest.subject` (`ingest/landing.py:344`) stays, as the value until
    wl.works sends one.
- **In the NWB file.** pynwb's `Subject` has no nickname field. It goes in `Subject.description`, which today
  reads "As wl.works' job request stated it (design spec section 9)." It becomes "Nickname <name>, as wl.works'
  record gives it. As wl.works' job request stated it (design spec section 9)." *(my call)*
  - The stub's value, which equals the subject id, is not stated.
  - Both texts are pinned by a test.
- **In the description.** `SubjectInfo` gains `nickname: str | None = None`, within version 3.
- **No correction in place.** `gather.described_subject` does not include the nickname, so a changed nickname
  marks no file stale. A file states the nickname stored when it was built. *(my call, after the builder check.)*
  - Correcting it in place would mean a redesign of `nwb/correct.py`. That code rewrites datasets and appends
    "Corrected" lines, and it rebuilds the description's subject from the file's datasets, so a nickname held only
    in text would read stale on every pass.
  - wl.works sends a nickname from 20b-1b's first request, so every canonical file is built with one. Only files
    built before 20b-1b lack it.

### 4.3 The task hash on listed runs

- `ListedRun.task_hash: str | None`. It is the run's `versions.task` from the rig's record when that is a content
  hash (`sha256:...`), else null: the rule `nwb/run_record.py`'s `task_hash` column uses, and the same name.
- **Stored.** The listing is built from the database alone, and `core.RunRecord` does not hold the hash today; the
  file reads it from `runs.jsonl` at build time.
  - So `core.RunRecord` gains `task_hash = null : varchar(255)`, written by the event stage from the same reading.
    *(my call, after the builder check.)*
- **One rule.** The same rule decides the listing's value and the file's, through one shared helper.

### 4.4 Paging

- Both `_get_since` paths accept `since` and `limit`, each at most once.
  - `limit` is one positive integer, with no upper bound. Absent, the whole page is returned, as today.
  - Any other key, a repeat, or a malformed value is a 422 naming both parameters.
- **`GET /sessions`.**
  - The sessions changed after `since` are ordered by their latest `change_seq`, as now. The page is the first
    `limit` of them.
  - `cursor` is the latest `change_seq` on the page. On an empty page it is `since`, or 0. *(Confirmed to
    wl.works.)*
  - A page shorter than `limit` means caught up.
- **`GET /nwb`.** The same, over activations and `nwb.NwbChange`.
- **Only the listed sessions are built.** `gather_all` and `build_entry` run only for the sessions on the page.
- **Announcements.** `docs/ops/lab-host-protocol.md` and `docs/pending-wl-works-amendments.md` announce it.
  wl.works sends `limit` only after the latter does.

### 4.5 Ordered times

- **Where.** `listing/entry.py::build_entry` checks every segment, run and block before it builds the entry.
  - A segment with `end_s < start_s` is dropped, and removed from every run's `segments`.
  - A run with `end_s < start_s` is dropped. No block starts inside it (`starts_inside` needs start <= t <= stop),
    so blocks are not dropped with it. A block in no listed run is flagged `block_outside_runs`, as today.
    *(my call, after the builder check.)*
  - A block with `end_s < start_s` is dropped from its run. A run left with no block this way gets no
    `run_without_block` flag, whose message ("it stopped before its first trial") would be false. *(my call.)*
  - Equal start and end is allowed.
- **One rule for the listing, `accept()` and the build.** A single helper names what ends before it starts:
  - **`accept()`.** `_check_runs` requires a canonical to assert every measured run in its montage's window. A run
    the rule drops is left out of that requirement. Otherwise its montage could never be requested, the outcome
    the requester rejected. Naming such a run is a 422 that names it and its times.
  - **The build.** It leaves such a run, block or file out of the file. The file's notes name each one, with its
    times.
- **The flag.**
  - `ListedFlagCode` gains `"ends_before_start"`. `ListedFlag` gains `segment_id: str | None = None`, beside
    `run_number` and `block_number`.
  - Each dropped item gets one flag:
    - `run_number` for a run;
    - `run_number` and `block_number` for a block, the run's when it has one;
    - `segment_id` for a file.
  - Its message names the item and both times, e.g. "run 3 ends at 101.250 s, before its start at 101.300 s: left
    out of this listing".
  - Unlike every flag before it, this one means something was withheld. The `ListedFlag` docstring and the
    protocol document say so.
- **The validators.** `ListedSegment`, `ListedRun` and `ListedBlock` each gain a model validator,
  `end_s >= start_s`. It is the backstop: with the checks above it cannot fire from `build_entry`.
  - It also covers the daemon's listing stage, which hashes the same entries.
  - Fields are not reordered and no names change.
- **Tests.**
  - One session with a reversed run, block and file is listed with the three dropped and flagged.
  - A second session on the same page is listed whole.
  - `GET /sessions` answers 200.
  - A canonical for the first session's montage that asserts every other run is accepted. One that names the
    reversed run is a 422.
  - Its file holds neither the reversed run nor the reversed block nor the file, and names them in its notes.

### 4.6 `metadata.experimenter`

- `experimenter: Annotated[str, Field(max_length=64)]`. Empty stays allowed: it gives a file with no experimenter
  (`nwb/gather.py:911`). wl.works sends the ELN's "Last, First" and never a system name (wl.works, 2026-11-24).

### 4.7 Schemas and announcements

- `docs/schemas/job_request.json`, `session_listing.json` and `nwb_description.json` are regenerated, and their
  tests show it.
- One block in `docs/pending-wl-works-amendments.md` names every change above, and what wl.works' vendored
  copies must take.
- wl.works gets the flag's field name and its place as soon as this design is approved, before the build. It
  pins at our merge.

## 5. Constraints

- **Contracts.** Pydantic models stay `extra="forbid"`. Fields added within a version default, so a description or
  listing written before reads unchanged.
- **Database.** Development databases are rebuilt, never migrated. `core.RunRecord` gains `task_hash`.
- **wl.yaml.** Run `wl-check` after any `wl.yaml` edit. No dependency changes are expected.
- **Testing.** Full suite on 3.11 and 3.13 at merge, with every reference set. Floats that go through trig are
  compared to a tolerance.

## 6. Approaches

1. **One branch, one merge** *(chosen)*. wl.works pins every field at one commit of ours.
2. **Two merges**: the listing side (task hash, paging, ordered times), then the request side (marks, nickname,
   experimenter). This would make wl.works pin twice and re-copy its vendored schemas twice. It buys nothing,
   since nothing here is urgent alone.
3. **For ordered times**, the requester weighed and rejected listing the session with nothing to request. It
   would block a whole session for one bad item.
   - Not listing the session at all was never offered: wl.works would never learn of it.

## 7. Left to the builder

- **How the shared task-hash helper is factored** (§4.3).
- **The test layout.**
- **The replacement test's fixture** (§2.8): whichever path `accept()` uses for a refused canonical's
  replacement, the test must send the replacement through `POST /jobs`.

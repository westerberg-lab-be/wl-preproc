# wl_preproc/responder/jobs.py
"""`JobRequest` -> rows. Design spec section 6.1. The only module here that writes.

Two sub-projects treated montage boundaries as a blocker before this module
existed: 1c-1 narrowed `submit()` to canonical activations because it had no
block set, and 1c-2 avoided `submit()` entirely because timebase and coverage
populate from `Session` keys alone. **The answer was already in the frozen
contract** (design spec section 1): `contracts.protocol.MetadataBundle`
carries `montage_boundaries` -- and, since design spec
`2026-10-01-session-listing-and-run-requests-design.md`, `runs` -- inbound
with EVERY request, and its own docstring says why -- "everything wl-preproc
needs from the ELN arrives in the request payload." `core.Montage`'s own
comment says it is "Sourced from wl.works `item_insertion` and nothing else".
So recording a montage here is not measuring or guessing at a boundary; it is
wl.works' own authored record, arriving by the exact route its frozen
contract already describes. *True when written.* A run is the other way
round: `core.Run` is this host's measurement, and what arrives is wl.works'
copy of it and its id for it, checked against the measurement and kept in
`core.RunAssertion`.

**What replaced the boundaries** (design spec `2026-11-24-montages-design.md`
sections 4.4, 4.6 and 5). This host now derives a session's montages from
wl.works' probe positions (`PUT /positions`) and its own census, and the
daemon's montage stage stores them (`montage/stage.py`), which `GET
/sessions` lists. `metadata.montage_boundaries` is retired as
`metadata.blocks` was, and a request names a listed montage in
`selection.montage_id`: `_listed_montage` reads it from the stored rows,
never deriving it again, and a montage that is not stored, or not listed
at the session's `positions_status`, is a 422 naming that status, never
created. So is a session whose positions changed since its montages were
derived (`_check_positions_seq`). Each probe's runs are among
the ones the montage lists as sortable for it, and with positions stored each
of `metadata.probes` names one of them (plan decision D14; `_check_probes`,
`_check_probe_runs`). The stage rewrites a session's montages only until its
first accepted request, and the activation's transaction holds the
derivation's lock and finds it unchanged since the checks read it
(`_hold_derivation`), so an activation never covers montages it was not
checked against.

**Two corrections carried from Task 4's review, applied here rather than
re-derived:**

1. **`accept()` opens no transaction of its own.** `submit()`/
   `submit_derivative()` each already guard on `dj.conn().in_transaction` and
   raise -- DataJoint transactions do not nest -- and `submit()`'s own
   docstring says directly that neither the ingest watcher nor the responder
   may wrap it to bundle it with other writes. So `accept()` builds and
   checks the `RunAssertion` rows and hands them to `submit*()`, which writes
   them, insert-if-absent, inside the transaction it opens and owns for the
   `Request`+`Activation` pair: a request refused there with a 409 records
   none of them (Plan B's final review, M1, and the leftovers' review). See
   `test_accept_refuses_to_run_inside_a_transaction`. *Until then `accept()`
   wrote `Montage` itself, as an un-transacted insert-if-absent before
   calling `submit*()`, and then handed `submit*()` the `Montage` rows too,
   until a request stopped sending them; true when written.* What it hands
   `submit*()` now besides is `_hold_derivation`, which `submit*()` calls
   first inside that transaction.
2. **`accept()` owns the montage window.** The window between a montage's
   `[start_s, end_s)` and the runs a file holds is checked here -- a check
   `submit_derivative` itself does not make, having no `Montage`/`Run`
   timing to compare against. See `_check_runs`.

**Existing `Montage` rows are never overwritten.** `submit*()`'s insert uses
`skip_duplicates=True`: wl.works owns this record, and a later request naming
different boundaries for a montage already on file is wl.works correcting its
own record -- their call to make explicitly, not something to infer from
whichever payload happened to arrive most recently. *True when written; a
request writes no `Montage` now.* A run already asserted
under one `works_run_id` and named under another, or an id already recorded
for one run named for another, is a `RunIdConflict`, a `409`: the two
disagree about which run it is. The same id for two runs within one request
is a `422`.

**Review round 1 (2026-08-16) found four more things, addressed here:**

- **C1 -- validate before writing, not after.** The first draft inserted
  `Montage` and `Block` rows and only THEN checked the window, so a rejected
  request permanently planted the very row that caused its own rejection --
  `skip_duplicates=True` then discarded every later correction, so the
  request that fixed the boundary was rejected too, citing the stale values
  it refused to replace. `accept()` now builds every candidate row, checks
  montage existence and every run against those candidates PLUS whatever is
  already on record, and only writes anything once every check has passed --
  so a rejected request leaves no residue at all. *Its montage half went with
  the boundaries: the montage is read from the stored rows, and the checks of
  the probes against it run here too, before anything is written.* Its test
  went with the boundaries; `test_a_request_from_a_stale_listing_is_a_run_
  mismatch_and_writes_nothing` and the montage refusals' `_nothing_written`
  in `tests/responder/test_jobs.py` hold it now.
- **C2 -- `selection["session_datetime"]` is coerced, not assumed to already
  be a `datetime`.** JSON has no datetime type and `docs/schemas/
  job_request.json` declares `selection` as a bare
  `{"type": "object", "additionalProperties": true}` with no `session_
  datetime` property at all, so real wire traffic hands this function a
  plain `str`. `_coerce_session_datetime` accepts a `datetime.datetime` or
  an ISO-8601 string and raises `ValueError` for anything else -- see that
  function's own docstring for the `AttributeError` this replaces.
- **I1 -- the whole payload is normalised, not one key.** The tzinfo-drop
  finding (see `_coerce_session_datetime`'s neighbour, the payload
  construction near the bottom of `accept()`) is not scoped to `selection.
  session_datetime`: any aware `datetime` anywhere in the stored payload
  reproduces the identical defect on a retry. `request.model_dump(mode=
  "json")` -- this project's own existing convention in `contracts/
  manifest.py`, `contracts/done.py` and `synth/peripherals.py`, the only one
  of four `model_dump()` call sites that had omitted it -- serialises every
  nested datetime to a deterministic ISO-8601 string before it is ever
  packed into the blob column, so nothing in the payload can carry a live
  datetime object for the codec to round-trip incorrectly in the first
  place. Confirmed directly: `model_dump(mode="json")` on the same aware
  input produces the identical string on every call, and that string
  round-trips through `datajoint.blob.pack`/`unpack` unchanged.
- **I2/I4 -- the columns this function writes are guarded before insert,
  matching `ingest/params.py`'s own stated convention** ("checked here,
  inside the validation step, rather than left for ... insert to discover as
  a raw `pymysql.err.DataError` -- which Task 8's watcher does not
  special-case"). The `montage_id` range is checked against the real column
  bound before any insert is attempted; the montage's and each run's own
  bounds now live on `contracts.protocol.MontageBoundary` and `RunEntry`, so
  a request breaking them cannot be built. *True when written; a request
  sends no montage now, and `selection.montage_id` is still checked against
  the column before it is read.*

**The whole-branch review (2026-08-16) found one more, fixed here:**

- **C1 -- a job for a session this host has never ingested was a `500`,
  which the protocol document tells wl.works to retry forever.** `accept()`
  validated montage existence, the window, subject length,
  every column bound and the DATETIME floor -- but not that `Session`
  itself exists, so `core.Montage.insert` hit the foreign key and the
  resulting `IntegrityError` became a retryable `500` (*true when written;
  this module writes no `Montage` now, and the check still comes first*). Creating the session
  is still not this function's job; DETECTING its absence now is. See
  `_require_landed_session` for the full reasoning, the measured
  before/after, and why the answer is `422` rather than `409`.

**A design gap this module once flagged is closed.** `core.Block` was
specified as wl-preproc's own measurement, and this function wrote wl.works'
asserted numbers into it, permanently. A request now asserts runs, which
`core.Run` measures from the recording; what wl.works asserts is checked
against that measurement as the request arrives and kept apart from it, in
`core.RunAssertion` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 3).
"""

from __future__ import annotations

import datetime
import math

from pydantic import ValidationError

from wl_preproc.contracts.protocol import JobRequest, ProbeEntry, RunEntry
from wl_preproc.events import agreement
from wl_preproc.ingest import landing
from wl_preproc.listing import order
from wl_preproc.montage import stored as stored_montages
from wl_preproc.responder.positions import stored_positions
from wl_preproc.schema import DEFAULT_PREFIX, core, pipeline
from wl_preproc.schema import request as schema_request

# The two keys `accept()` itself reads out of an arbitrary, wl.works-supplied
# `selection` dict. `subject` is deliberately NOT among them: session identity
# comes from `metadata.subject` (the ELN's own record), never from anything
# named "subject" inside `selection` -- see `accept()`'s own docstring.
_REQUIRED_SELECTION_KEYS = ("session_datetime", "montage_id")

# Column bounds, read directly from wl_preproc/schema/core.py's declared
# types -- `montage_id` does not declare "unsigned" (unlike
# core.Segment's former "int unsigned" barcode key), so its range is SIGNED.
# Checked before any insert, matching ingest/params.py's own stated
# convention for paramset_type/PARAMSET_TYPE_MAX_LEN: a value that is a
# syntactically fine Python int/str can still be too big/long for the column
# it is about to be inserted into, and finding that out from a raw
# pymysql.err.DataError leaves Task 8's handler with an exception type its
# documented ValueError/DataJointError contract does not cover -- confirmed
# with each guard removed in turn against a live column, when this module
# also wrote core.Block: 1406 "Data too long" (a string column), 1264 "Out of
# range value" (an integer one), and 1265 "Data truncated" (a non-finite
# float into a double -- not 1264, corrected after actually reproducing it
# rather than assuming the same errno as the integer case). None of the three
# appears in DataJoint's MySQL adapter's translated-error list.
_MONTAGE_ID_RANGE = (-128, 127)  # core.Montage.montage_id : tinyint
# request.Activation.activation_id : int, and a superseded one is never
# negative: the allocator starts at 0.
_ACTIVATION_ID_RANGE = (0, 2**31 - 1)
# MySQL's DATETIME floor. Python's own datetime.MINYEAR (1) is far below it,
# and DataJoint's bare `datetime` column type validates nothing on its own.
_DATETIME_MIN_YEAR = 1000


def _require_selection_keys(selection: dict) -> None:
    missing = [key for key in _REQUIRED_SELECTION_KEYS if key not in selection]
    if missing:
        raise ValueError(
            f"selection is missing required key(s) {missing}; got {sorted(selection)}"
        )


def _reject_oversized_subject(subject: str) -> None:
    if len(subject) > landing.SUBJECT_MAX_LEN:
        raise ValueError(
            f"metadata.subject {subject!r} is {len(subject)} characters, over "
            f"the {landing.SUBJECT_MAX_LEN}-character limit element-animal's "
            "Subject.subject column enforces (landing.SUBJECT_MAX_LEN)"
        )


def _coerce_session_datetime(value) -> datetime.datetime:
    """A `datetime.datetime`, or an ISO-8601 string, become the value
    `landing.to_naive_utc` accepts. Anything else raises `ValueError`.

    Real wire traffic never carries a `datetime.datetime` at all: JSON has no
    datetime type, `docs/schemas/job_request.json` declares `selection` as a
    bare `{"type": "object", "additionalProperties": true}` with no
    `session_datetime` property (let alone a `format: date-time`), and
    `selection: dict[str, Any]` makes pydantic coerce nothing inside it --
    confirmed directly against the committed schema. So
    `selection["session_datetime"]` arrives as a plain `str` over HTTP, and
    handing that straight to `to_naive_utc` (whose own signature is typed
    `datetime.datetime`) fails with `AttributeError: 'str' object has no
    attribute 'tzinfo'` -- outside this module's documented `ValueError`
    contract, and Task 8's design (a `JobRequest` that validates against
    `JobRequest`'s own schema is, by definition, not "a malformed body")
    would have to map an `AttributeError` it never expected.

    `datetime.fromisoformat` (Python 3.11+, this project's floor) parses a
    "Z" suffix, a numeric UTC offset, or no offset at all -- whatever real
    wl.works traffic sends. A value already naive (no offset in the string)
    is returned as `fromisoformat` parses it and then passed through
    `to_naive_utc` unchanged, exactly as that function's own docstring
    describes for an already-naive input.
    """
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"selection['session_datetime'] {value!r} is not a valid "
                f"ISO-8601 datetime string: {exc}"
            ) from exc
    raise ValueError(
        "selection['session_datetime'] must be a datetime.datetime or an "
        f"ISO-8601 string, got {type(value).__name__}: {value!r}"
    )


def _lifecycle_role(selection: dict, run_numbers: list[int]) -> bool:
    """Whether the request asks for a canonical, from `selection`'s optional
    `role` and `supersedes_activation_id` (design spec
    `2026-09-30-canonical-lifecycle-design.md` section 3). Without a `role`,
    `run_numbers` makes a derivative and none a canonical over the whole
    montage, as `block_ids` did (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3.1).
    Raises `ValueError` (a `422`) for the combinations section 3 refuses,
    before anything is written."""
    role = selection.get("role")
    if role not in (None, "canonical", "derivative"):
        raise ValueError(f"selection['role'] must be 'canonical' or 'derivative', got {role!r}")
    # A null means absent, as a null `role` does (the 2b final review's M10).
    if selection.get("supersedes_activation_id") is not None:
        if role != "canonical":
            raise ValueError(
                "selection['supersedes_activation_id'] needs selection['role'] == 'canonical': only "
                "a canonical supersedes another (parent spec section 8.3)"
            )
        _reject_out_of_range_int(selection["supersedes_activation_id"],
                                 name="selection['supersedes_activation_id']", bounds=_ACTIVATION_ID_RANGE)
    if role == "derivative" and not run_numbers:
        raise ValueError("selection['role'] 'derivative' needs run_numbers: a derivative is its run set")
    return role == "canonical" or (role is None and not run_numbers)


class RunIdConflict(Exception):
    """A run already recorded under one wl.works id, named again under
    another (design spec `2026-10-01-session-listing-and-run-requests-design.md`
    section 3.2), or an id already recorded for one run of the session named
    for another (its amendment 26): the two disagree about which run this
    is, which resending cannot fix. `server.py` answers it with a 409, as for
    a reused key."""


_REBUILD = "rebuild the request from a fresh GET /sessions"
# The statuses `GET /sessions` lists montages at (plan decision D13).
_LISTED_STATUSES = ("placed", "not_needed")


def _run_numbers(value, *, name: str) -> list[int]:
    if not isinstance(value, list) or any(isinstance(n, bool) or not isinstance(n, int) for n in value):
        raise ValueError(f"{name} must be a list of run numbers, got {value!r}")
    return value


def _ends_before_start(run: dict) -> bool:
    return order.ends_before_start(run["run_start_time"], run["run_stop_time"])


def _left_out(run: dict) -> str:
    """The run as `GET /sessions` names it when it leaves the run out."""
    return order.run_message(run["run_number"], run["run_start_time"], run["run_stop_time"], where="listing")


def _cannot_be_requested(run: dict) -> str:
    return f"{_left_out(run)}; it cannot be requested"


def _check_runs(session_key: dict, montage_row: dict, asserted: list[RunEntry], run_numbers: list[int],
                canonical: bool) -> tuple[list[int], list[dict]]:
    """The file's runs, and the `core.RunAssertion` rows to record, after
    checking every asserted run against the measured `core.Run` as the
    request arrives (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3.2).

    A canonical holds every measured run whose start lies in its montage's
    window, and each must be asserted here; a derivative holds the runs it
    names, each measured, in the window, and asserted here or before.

    **A run that ends before it starts is in neither** (design spec
    `2026-11-24-wl-works-requests-design.md` section 4.5): `GET /sessions`
    leaves it out, so wl.works can never assert it, and a canonical need not.
    Asserting or naming one is a 422 that names it and both its times."""
    measured = {row["run_number"]: row for row in (core.Run & session_key).to_dicts()}
    if not measured:
        raise ValueError(
            f"session {session_key['subject']}/{session_key['session_datetime'].isoformat()} has no measured "
            "run on this host yet: its event codes are not yet read, or the rig sent no run markers. Resend "
            "once GET /sessions lists its runs."
        )
    by_number: dict[int, RunEntry] = {}
    for entry in asserted:
        if entry.run_number in by_number:
            raise ValueError(f"metadata.runs names run {entry.run_number} twice")
        by_number[entry.run_number] = entry
    # An `animal_session_run` records the one measured run it came from, so
    # one id never names two runs (Plan B's final review, M2).
    named_for: dict[str, int] = {}
    for number, entry in sorted(by_number.items()):
        if entry.works_run_id in named_for:
            raise ValueError(f"metadata.runs names works_run_id {entry.works_run_id!r} for runs "
                             f"{named_for[entry.works_run_id]} and {number}")
        named_for[entry.works_run_id] = number
    rows = []
    for number, entry in sorted(by_number.items()):
        run = measured.get(number)
        if run is None:
            raise ValueError(f"run {number} is not a measured run of this session; {_REBUILD}")
        if _ends_before_start(run):
            raise ValueError(_cannot_be_requested(run))
        for end, asserted_s, measured_s in (("start", entry.start_s, run["run_start_time"]),
                                            ("end", entry.end_s, run["run_stop_time"])):
            if abs(asserted_s - measured_s) > agreement.RUN_AGREEMENT_TOLERANCE_S:
                raise ValueError(f"run {number}'s {end}, {asserted_s} s, is not the measured {measured_s} s; "
                                 f"{_REBUILD}")
        rows.append({**session_key, "run_number": number, "works_run_id": entry.works_run_id,
                     "start_s": entry.start_s, "end_s": entry.end_s})
    on_record = {row["run_number"]: row["works_run_id"] for row in (core.RunAssertion & session_key).to_dicts()}
    for row in rows:
        known = on_record.get(row["run_number"])
        if known is not None and known != row["works_run_id"]:
            raise RunIdConflict(f"run {row['run_number']} is recorded with works_run_id {known!r}; this request "
                                f"names {row['works_run_id']!r}")
        for number, works_run_id in sorted(on_record.items()):
            if works_run_id == row["works_run_id"] and number != row["run_number"]:
                raise RunIdConflict(f"works_run_id {works_run_id!r} is recorded for run {number}; this request "
                                    f"names it for run {row['run_number']}")
    in_window = [number for number, run in sorted(measured.items())
                 if montage_row["start_s"] <= run["run_start_time"] < montage_row["end_s"]]
    window = [number for number in in_window if not _ends_before_start(measured[number])]
    bounds = f"montage {montage_row['montage_id']}'s window [{montage_row['start_s']}, {montage_row['end_s']})"
    if canonical:
        if not window and in_window:
            # Review focus 5: refused, naming them, never an empty file.
            raise ValueError(f"{bounds} holds no run that can be requested: "
                             + "; ".join(_left_out(measured[number]) for number in in_window))
        if not window:
            raise ValueError(f"{bounds} holds no measured run; {_REBUILD}")
        missing = [number for number in window if number not in by_number]
        if missing:
            raise ValueError(f"measured run(s) {missing} lie in {bounds} and the request does not assert them; "
                             f"{_REBUILD}")
        return window, rows
    named = sorted(set(run_numbers))
    for number in named:
        if number in measured and _ends_before_start(measured[number]):
            raise ValueError(_cannot_be_requested(measured[number]))
    for problem, numbers in (("are not measured runs of this session", [n for n in named if n not in measured]),
                             (f"outside {bounds}", [n for n in named if n in measured and n not in window]),
                             ("asserted neither in metadata.runs nor before",
                              [n for n in named if n not in by_number and n not in on_record])):
        if numbers:
            raise ValueError(f"selection names run(s) {numbers} {problem}; {_REBUILD}")
    return named, rows


def _check_probes(session_key: dict, probes: list[ProbeEntry]) -> None:
    """With positions stored, each of `metadata.probes` names one of them: the
    same serial at the same insertion number (plan decision D14). With none
    stored (a session with no ephys), probes are recorded as they come."""
    stored = stored_positions(session_key)
    if stored is None:
        return
    _seq, positions = stored
    held = {(position["serial"], position["insertion_number"]) for position in positions}
    for probe in probes:
        if (probe.serial, probe.insertion_number) not in held:
            named = ", ".join(f"probe {serial} at insertion_number {number}"
                              for serial, number in sorted(held)) or "none"
            raise ValueError(
                f"metadata.probes names probe {probe.serial} at insertion_number {probe.insertion_number}, and no "
                f"stored position is that probe at that insertion: the positions stored for "
                f"{session_key['subject']}/{session_key['session_datetime'].isoformat()} are {named}; send PUT "
                f"/positions, or {_REBUILD}")


def _check_probe_runs(probes: list[ProbeEntry], probe_runs, file_runs: list[int], montage_probes: dict[str, dict],
                      positioned: bool, *, montage_id: int) -> None:
    """Every probe's runs are stated in full, and only the file's (design
    spec `2026-10-01-session-listing-and-run-requests-design.md` section 3.1;
    wl.works' Plan 20 rule: the record is the run set each probe resolved
    to, not the exclusions).

    Each is also among the runs the montage lists as sortable for that probe
    (`montage_probes`, by serial; design spec `2026-11-24-montages-design.md`
    section 4.4). With positions stored (`positioned`), a probe the montage
    does not list holds no position in it and sorts no run there (plan
    decision D14). A derivative's runs are each of its probes', so it is
    checked here as a canonical stating them is: never narrowed."""
    serials = sorted({probe.serial for probe in probes})
    if probe_runs is None:
        if serials:
            raise ValueError(f"selection.probe_runs must give every probe's runs; metadata.probes names {serials}")
        return
    if not isinstance(probe_runs, dict) or any(not isinstance(serial, str) for serial in probe_runs):
        raise ValueError(f"selection.probe_runs must map each probe's serial to its runs, got {probe_runs!r}")
    if sorted(probe_runs) != serials:
        raise ValueError(f"selection.probe_runs names {sorted(probe_runs)}, and metadata.probes names {serials}: "
                         "every probe's runs are stated, and only theirs")
    for serial, numbers in sorted(probe_runs.items()):
        outside = sorted(set(_run_numbers(numbers, name=f"selection.probe_runs[{serial!r}]")) - set(file_runs))
        if outside:
            raise ValueError(f"probe {serial}'s runs {outside} are not among the file's runs {file_runs}")
    for serial, numbers in sorted(probe_runs.items()):
        listed = montage_probes.get(serial)
        if listed is None and not positioned:
            continue
        sortable = sorted(listed["sortable_runs"]) if listed is not None else []
        unsortable = sorted(set(numbers) - set(sortable))
        if not unsortable:
            continue
        if listed is None:
            raise ValueError(f"probe {serial} holds no position in montage {montage_id}, so it sorts no run there; "
                             f"the request gives it run(s) {unsortable}")
        raise ValueError(f"probe {serial}'s run(s) {unsortable} are not among its sortable runs {sortable} in "
                         f"montage {montage_id}")


def _reject_out_of_range_int(value, *, name: str, bounds: tuple[int, int]) -> None:
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if not (low <= value <= high):
        raise ValueError(f"{name}={value} is outside the column's range [{low}, {high}]")


def _require_landed_session(session_key: dict) -> None:
    """`ValueError` -- and so `422`, not `500` -- when no `Session` row
    exists for this request's `(subject, session_datetime)`.

    **`accept()` does not create the session, and must not.** That is
    `ingest/landing.py`'s job, and `tests/responder/test_jobs.py`'s
    `landed_session` fixture says so in as many words. What is new here is
    only that the ABSENCE is detected rather than discovered by the
    database: `core.Montage.insert` below carries a foreign key to
    `pipeline.Session`, so without this check the very next write raises
    `IntegrityError`, which `handler.py` maps -- correctly, for every other
    member of that family -- to a `500` (*true when written; the writes that
    carry that foreign key now are `RunAssertion`'s and the request's own*).
    Measured live over a real socket before this check existed:

    ```
    POST /jobs, subject/session_datetime never ingested
     -> 500 {"error": "IntegrityError: Cannot add or update a child row: a
        foreign key constraint fails (`e2e_core`.`montage`, CONSTRAINT
        `montage_ibfk_1` FOREIGN KEY (`subject`, `session_datetime`)
        REFERENCES `e2e_session`.`session` ...)"}
    ```

    Three separately-correct decisions composed into one wrong answer on
    the wire: this module does not own session existence, `handler.py` maps
    every non-`KeyReuseError` DataJoint error to `500` deliberately (a lost
    connection IS retryable), and `docs/ops/lab-host-protocol.md` documents
    `500` as retryable. Together they told a conforming wl.works client to
    retry, forever, a request that cannot succeed until something outside
    the retry loop happens. The 500 body also leaked schema, table and
    constraint names to the caller.

    **The trigger is the ordinary case, not an exotic one.** wl.works knows
    a session exists from the ELN hours before its data transfer lands on
    this host, so any button pressed in that window arrives here first. The
    honest answer is "not yet", and it belongs in the status code: `422`
    (the server understood the request and cannot process the instructions
    it contains), joining `accept()`'s existing `ValueError` family and the
    protocol document's own `422` list -- no new status on the wire, no new
    branch in their client.

    `409` was the considered alternative and was ruled out: this document's
    own meaning for it is "the remedy is outside the retry loop, tell a
    human", which fits an ELN typo but not a transfer still in flight, and
    it would force wl.works to parse message text to tell key reuse from a
    session that has simply not arrived yet.

    `pipeline.Session` is read as a module attribute at call time, never
    bound by a `from ... import Session` at this module's import time:
    `pipeline.activate()` REBINDS that global to the activated table (see
    its own `global Session, Subject`), so a name bound earlier would point
    at the unactivated placeholder forever.
    """
    if not (pipeline.Session & session_key):
        raise ValueError(
            f"session {session_key['subject']}/"
            f"{session_key['session_datetime'].isoformat()} is not yet on "
            "record on this host: no Session row exists for it, so there is "
            "nothing to attach a montage, a run or a request to. wl.works "
            "knows a session exists from the ELN before its data transfer "
            "lands here; until ingest has landed it, this host cannot accept "
            "a job for it. Resend once the transfer has completed."
        )


def _record_subject_details(subject: str, details) -> None:
    """wl.works' own record of the animal, when the request carries it,
    written into element-animal's tables: `Subject.sex`,
    `Subject.subject_birth_date`, `Subject.subject_nickname` and `Subject.Species` (design spec
    `2026-09-28-nwb-builder-design.md` section 9). Absent, nothing about the
    subject changes. The latest request wins: the ELN is the authority."""
    if details is None:
        return
    from wl_preproc.schema import pipeline

    row = {"subject": subject, "sex": details.sex}
    if details.date_of_birth is not None:
        row["subject_birth_date"] = details.date_of_birth
    if details.nickname is not None:
        row["subject_nickname"] = details.nickname
    pipeline.subject.Subject.update1(row)
    if details.species is not None:
        pipeline.subject.Species.insert1({"species": details.species}, skip_duplicates=True)
        pipeline.subject.Subject.Species.insert1(
            {"subject": subject, "species": details.species}, replace=True
        )


def _record_probe_reports(session_key: dict, probes, prefix: str) -> None:
    """Every `metadata.probes` entry, as `ephys.InsertionReport` (the latest
    request wins) and its assignment as an `ephys.AreaAssignment`
    (append-only). Design spec `2026-09-30-nwb-probes-design.md` section
    2.2."""
    if not probes:
        return
    from wl_preproc.schema import ephys

    ephys.activate(prefix=prefix)
    for probe in probes:
        target = probe.target
        ephys.InsertionReport.insert1(
            {
                **session_key,
                "insertion_number": probe.insertion_number,
                "probe_serial": probe.serial,
                "trajectory_id": probe.trajectory_id,
                "target_area": target.area if target else None,
                "target_atlas": target.atlas if target else None,
                "target_atlas_level": target.atlas_level if target else None,
            },
            replace=True,
        )
        assignment = probe.area_assignment
        if assignment is not None:
            asserted_at = assignment.asserted_at
            if asserted_at.tzinfo is not None:
                asserted_at = asserted_at.astimezone(datetime.UTC).replace(tzinfo=None)
            ephys.AreaAssignment.insert1(
                {**session_key, "insertion_number": probe.insertion_number, "asserted_at": asserted_at,
                 "area": assignment.area, "source": assignment.source},
                skip_duplicates=True,
            )


def _listed_montage(session_key: dict, montage_id: int) -> tuple[dict | None, dict]:
    """The session's stored derivation (`stored_montages.derivation_row`) and
    the montage `montage_id` as this host lists it (`{montage_id, start_s,
    end_s, probes}`), from the stored rows and never derived again (design
    spec `2026-11-24-montages-design.md` section 5). A `ValueError` (a 422)
    naming the session's `positions_status` when no such montage is stored,
    or the status is not one `GET /sessions` lists montages at (`placed`,
    `not_needed`; plan decision D13).

    The derivation is read first: a rewrite committed after it changes the
    row `_hold_derivation` compares it with, whatever was read after it."""
    derivation = stored_montages.derivation_row(session_key)
    stored = stored_montages.read_stored([session_key]).get((session_key["subject"],
                                                              session_key["session_datetime"]))
    montage = next((m for m in (stored.montages if stored else ()) if m["montage_id"] == montage_id), None)
    status = stored.status if stored else "waiting"
    if montage is None or status not in _LISTED_STATUSES:
        raise ValueError(f"no montage {montage_id} is listed for {session_key['subject']}/"
                         f"{session_key['session_datetime'].isoformat()}: positions_status is {status}; send PUT "
                         f"/positions, or {_REBUILD}")
    return derivation, montage


def _check_positions_seq(session_key: dict, derivation: dict | None, positions_seq: int | None) -> None:
    """A 422 when the session's stored positions are not the seq its montages
    were derived from: positions changed since, and the next pass derives
    and lists them again (plan decision D17). Accepted now, the request
    would fix the old montages under the new positions (D8). With no
    positions stored there is nothing to compare."""
    derived = derivation["positions_seq"] if derivation else None
    if positions_seq is not None and positions_seq != derived:
        since = "with no positions" if derived is None else f"(seq {derived})"
        raise ValueError(f"session {session_key['subject']} {session_key['session_datetime'].isoformat()}: its "
                         f"positions changed (seq {positions_seq}) since its montages were derived {since}; "
                         f"send the request again once GET /sessions lists seq {positions_seq}")


def _locked_positions_seq(session_key: dict) -> int | None:
    """The session's stored `PositionSet.positions_seq`, read `FOR UPDATE`
    inside the caller's transaction: the latest committed seq, which a `PUT
    /positions` cannot change until that transaction ends. An exact
    primary-key match, as `stored_montages.lock_derivation`'s is."""
    import datajoint as dj

    key = {name: session_key[name] for name in ("subject", "session_datetime")}
    sql = (schema_request.PositionSet & key).proj("positions_seq").make_sql() + " FOR UPDATE"
    rows = list(dj.conn().query(sql, as_dict=True).fetchall())
    return int(rows[0]["positions_seq"]) if rows else None


def _hold_derivation(session_key: dict, derivation: dict | None) -> None:
    """Inside the activation's transaction, before it writes: take the
    session's derivation lock and refuse the request if the derivation is not
    the one its checks read, or if its positions changed since it was
    derived (`_check_positions_seq`). Until this transaction ends the
    montage stage cannot rewrite the session (`montage/stored.py`'s module
    docstring) and a `PUT /positions` cannot store another seq, and once it
    has ended with an activation the stage never derives the session again
    (plan decision D8), so what the checks read is what the activation
    covers. Both reads lock, so neither comes from the snapshot the
    transaction began with."""
    held = stored_montages.lock_derivation(session_key)
    if held != derivation:
        status = held["positions_status"] if held else "waiting"
        raise ValueError(f"the montages of {session_key['subject']}/{session_key['session_datetime'].isoformat()} "
                         f"were derived again while this request was checked: positions_status is {status}; "
                         f"{_REBUILD}")
    _check_positions_seq(session_key, held, _locked_positions_seq(session_key))


def _as_current_payload(stored: dict) -> dict:
    """A payload recorded under an earlier contract, as today's `JobRequest` would dump it.

    A field added with a default since the request was stored has no key in the stored payload, and
    `accept()` dumps the retried body with that key present, so the two would differ and an honest
    retry would be refused as key reuse. Re-reading the stored payload through the model fills in
    exactly those defaults. A payload today's model refuses is left as it is: it is not an honest
    retry of today's body, and the raw comparison says so.
    """
    try:
        return JobRequest.model_validate(stored).model_dump(mode="json")
    except ValidationError:
        return stored


def accept(request: JobRequest, prefix: str = DEFAULT_PREFIX) -> dict:
    """A validated `JobRequest` becomes `RunAssertion`/`Request`/`Activation`
    rows, with the activation's runs and each probe's, over a montage this
    host listed. Design spec section 6.1, and design specs
    `2026-10-01-session-listing-and-run-requests-design.md` section 3 and
    `2026-11-24-montages-design.md` section 4.6. Returns the `Activation`
    primary key.

    Raises `ValueError` for a request that cannot be honoured: a `selection`
    missing `session_datetime` or `montage_id`; the retired
    `selection.block_ids`; a `metadata.subject` longer than
    `landing.SUBJECT_MAX_LEN`; a `session_datetime` that is neither a
    `datetime.datetime` nor a parseable ISO-8601 string; a
    `(subject, session_datetime)` with no `Session` row on this host yet
    (`_require_landed_session` -- the ordinary ELN-before-transfer case,
    which used to reach the database and come back as a `500` telling
    wl.works to retry forever); a `montage_id` that cannot fit its column; a
    `montage_id` this host has not listed (`_listed_montage`); positions
    changed since the montages were derived (`_check_positions_seq`); a probe that
    names no stored position, or runs a probe cannot be sorted on in the
    montage (`_check_probes`, `_check_probe_runs`); a derivation rewritten
    while the request was checked (`_hold_derivation`); or any run that does
    not match what this host measured (`_check_runs`, `_check_probe_runs`),
    including one `works_run_id` named for two runs. Raises `RunIdConflict` for a run already asserted
    under another `works_run_id`, or an id already recorded for another run. All of these are checked
    against BUILT-BUT-NOT-YET-WRITTEN candidate rows before anything is
    actually inserted (review C1): a rejected request leaves no
    `RunAssertion` residue behind for a later, corrected request to trip
    over.

    Session identity is `metadata.subject` plus `selection["session_datetime"]`,
    normalised through `landing.to_naive_utc` -- the one conversion every
    other datetime key in this codebase goes through, so this key lands on
    the exact naive value `ingest/landing.py` would already have written for
    the same session (see that module's own docstring on why two call sites
    converting differently is how two "equal" keys stop being equal).
    `selection`'s own `subject`, if wl.works ever sends one, is not read: the
    ELN's record of who this is is `metadata.subject`.

    `selection["run_numbers"]`, when present and non-empty, makes this a
    derivative holding those runs (`submit_derivative`); its absence, or an
    empty list, makes it a canonical holding every measured run of its
    montage, with each probe's runs in `selection["probe_runs"]` (`submit`).
    """
    selection = request.selection
    _require_selection_keys(selection)
    metadata = request.metadata
    # Before anything reads the database: a malformed selection is the
    # caller's to fix, whatever this host holds.
    if selection.get("block_ids"):
        raise ValueError("selection.block_ids is retired: a derivative names its runs in selection.run_numbers, "
                         "and a canonical holds every run of its montage, each probe's in selection.probe_runs")
    run_numbers = _run_numbers(selection.get("run_numbers") or [], name="selection.run_numbers")
    probe_runs = selection.get("probe_runs")
    canonical = _lifecycle_role(selection, run_numbers)
    if canonical and run_numbers:
        raise ValueError("selection.run_numbers is a derivative's: a canonical holds every run of its montage")
    if not canonical and probe_runs is not None:
        raise ValueError("selection.probe_runs is a canonical's: a derivative's runs are its run_numbers")

    _reject_oversized_subject(metadata.subject)

    session_datetime = landing.to_naive_utc(_coerce_session_datetime(selection["session_datetime"]))
    if session_datetime.year < _DATETIME_MIN_YEAR:
        raise ValueError(
            f"selection['session_datetime'] year {session_datetime.year} is "
            f"before {_DATETIME_MIN_YEAR}, MySQL DATETIME's floor"
        )

    montage_id = selection["montage_id"]
    _reject_out_of_range_int(montage_id, name="selection['montage_id']", bounds=_MONTAGE_ID_RANGE)

    session_key = {"subject": metadata.subject, "session_datetime": session_datetime}
    montage_key = {**session_key, "montage_id": montage_id}

    # ---- Phase 1: build every candidate row and validate everything
    # against them plus what already exists, before writing anything at all
    # (review C1). ----

    schema_request.activate(prefix=prefix)

    _require_landed_session(session_key)

    # The montage this host listed, never one the request describes (design
    # spec `2026-11-24-montages-design.md` section 4.6).
    derivation, montage = _listed_montage(session_key, montage_id)
    positions = stored_positions(session_key)
    # Checked again under the lock (`_hold_derivation`); here first, so a
    # probe is not refused against positions the montages do not come from.
    _check_positions_seq(session_key, derivation, positions[0] if positions else None)

    # Correction 2: accept() owns the montage window (module docstring).
    file_runs, run_assertion_rows = _check_runs(session_key, montage, metadata.runs, run_numbers, canonical)
    _check_probes(session_key, metadata.probes)
    if not canonical:
        # A derivative is the unit a sort runs over (decision 3), so each
        # probe the request names covers all of its runs; the request
        # states no per-probe list for one (Plan B's final review, I1).
        probe_runs = {probe.serial: file_runs for probe in metadata.probes}
    _check_probe_runs(metadata.probes, probe_runs, file_runs, {p["serial"]: p for p in montage["probes"]},
                      positions is not None, montage_id=montage_id)

    # ---- Phase 2: every check above passed. Only now does anything get
    # written. ----

    _record_subject_details(metadata.subject, metadata.subject_details)
    _record_probe_reports(session_key, metadata.probes, prefix)
    # Step 2 (design spec section 6.1): wl.works' id and copy of each run it
    # asserts, insert-if-absent, are written by `submit*()` inside its own
    # transaction, so a request it refuses (a 409) records none of them;
    # `_check_runs` has already refused a run named under a second id. That
    # transaction first holds the derivation the checks above read
    # (`_hold_derivation`).
    def hold() -> None:
        _hold_derivation(session_key, derivation)

    # The payload stored as evidence ("the request as received", Request's
    # own comment). mode="json" -- this project's own existing convention in
    # contracts/manifest.py, contracts/done.py and synth/peripherals.py --
    # serialises every nested value, including any datetime anywhere in
    # `selection`/`parameters`/`metadata`, to its deterministic JSON form
    # before it is ever packed into the blob column (review I1). Left as
    # plain Python objects, an aware datetime specifically would compare
    # unequal to itself on a genuine retry: DataJoint's blob codec drops a
    # datetime's tzinfo on its very first round trip through the database
    # (confirmed directly -- pack/unpack an aware value and it comes back
    # naive), so a second accept() call for the same idempotency_key would
    # build a fresh, still-aware payload and compare it against the first
    # call's now-naive stored copy -- and Python treats an aware and a naive
    # datetime as unequal under `==`/`!=` (it never raises, it just compares
    # false), so `_reject_key_reuse`'s `stored != given` would report a
    # difference that is not real and refuse an honest retry as key reuse.
    # mode="json" sidesteps this entirely rather than patching around it:
    # confirmed directly that it renders the same aware datetime to the
    # identical ISO-8601 string on every call, and that string round-trips
    # through datajoint.blob.pack/unpack byte-for-byte, because a string
    # carries no tzinfo for the codec to drop in the first place. See
    # test_accept_is_idempotent_on_the_same_key and
    # test_accept_normalises_an_aware_datetime_anywhere_in_the_stored_payload.
    payload = request.model_dump(mode="json")

    if not canonical:
        return schema_request.submit_derivative(
            idempotency_key=request.idempotency_key,
            task_type=request.domain,
            origin="wl_works",
            selection=montage_key,
            run_numbers=file_runs,
            payload=payload,
            requested_by=metadata.experimenter,
            probe_runs=probe_runs,
            run_assertions=run_assertion_rows,
            read_stored_payload=_as_current_payload,
            guard=hold,
        )

    if selection.get("supersedes_activation_id") is not None:
        return schema_request.submit_replacement(
            idempotency_key=request.idempotency_key,
            task_type=request.domain,
            origin="wl_works",
            selection=montage_key,
            payload=payload,
            requested_by=metadata.experimenter,
            supersedes_activation_id=selection["supersedes_activation_id"],
            run_numbers=file_runs,
            probe_runs=probe_runs,
            run_assertions=run_assertion_rows,
            read_stored_payload=_as_current_payload,
            guard=hold,
        )
    return schema_request.submit(
        idempotency_key=request.idempotency_key,
        task_type=request.domain,
        origin="wl_works",
        selection=montage_key,
        payload=payload,
        requested_by=metadata.experimenter,
        run_numbers=file_runs,
        probe_runs=probe_runs,
        run_assertions=run_assertion_rows,
        read_stored_payload=_as_current_payload,
        guard=hold,
    )

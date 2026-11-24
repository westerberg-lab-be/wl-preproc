# tests/responder/test_jobs.py
"""`JobRequest` -> rows. Design spec section 6.1."""

from __future__ import annotations

import datetime
import json
import re

import pytest

from tests.positions import put, seed_group, seed_montage
from wl_preproc.contracts.protocol import JobRequest, MetadataBundle

# The runs the event stage measured for every landed session here (design
# spec `2026-10-01-session-listing-and-run-requests-design.md` section 3):
# run 3 lies outside montage 0's window [0, 12), inside montage 1's [12, 24).
_RUNS = [(1, 0.0, 4.0), (2, 5.0, 11.0), (3, 13.0, 16.0)]
_SERIAL = "19011110001"
# The montage most requests here name, as this host lists it (design spec
# `2026-11-24-montages-design.md` section 4.6): stored by `seed_montage`.
_M0 = {"montage_id": 0, "start_s": 0.0, "end_s": 12.0}


def _asserted(runs=_RUNS, ids: dict | None = None) -> list[dict]:
    """wl.works' copy of each run, as `GET /sessions` listed it, and its id."""
    return [{"run_number": number, "start_s": start, "end_s": stop,
             "works_run_id": (ids or {}).get(number, f"wr-{number}")} for number, start, stop in runs]


@pytest.fixture
def landed_session(dj_conn, prefix):
    """A `(subject, session_datetime)` with Lab/Subject/Session already on
    file, and its runs measured -- the state `ingest/landing.py`'s
    `land_session` and the event stage would already have produced before
    any job request naming this session could arrive. `runs=()` leaves the
    session landed and its runs not yet read. Each of `montages` is stored as
    the montage stage would list it (`tests/positions.py::seed_montage`), as
    the daemon would have before wl.works could name one.

    `accept()` (design spec section 6.1, steps 1-4) is scoped to
    `Montage`/`RunAssertion`/`Request`/`Activation`; it is not what creates
    `Session`, its `Subject` parent or its `Run` rows. Mirrors
    `tests/schema/test_request.py`'s own `selection` fixture for the identical
    reason, stated there.
    """
    from wl_preproc.schema import core, pipeline
    from wl_preproc.schema import request as schema_request

    schema_request.activate(prefix=prefix)

    def _land(subject: str, session_datetime: datetime.datetime, runs=_RUNS, montages=()) -> dict:
        pipeline.lab.Lab.insert1(
            {"lab": "wl", "lab_name": "W", "address": "y", "time_zone": "UTC"},
            skip_duplicates=True,
        )
        pipeline.subject.Subject.insert1(
            {
                "subject": subject,
                "sex": "M",
                "subject_birth_date": datetime.date(2020, 1, 1),
                "subject_description": "",
            },
            skip_duplicates=True,
        )
        key = {"subject": subject, "session_datetime": session_datetime}
        pipeline.Session.insert1(key, skip_duplicates=True)
        if runs:
            core.Run.insert([{**key, "run_number": number, "task_type": 0, "run_start_time": start,
                              "run_stop_time": stop, "closed": 1} for number, start, stop in runs],
                            skip_duplicates=True)
        for montage in montages:
            seed_montage(key, montage)
        return key

    return _land


def _request(
    *,
    subject: str,
    session_datetime,
    idempotency_key: str,
    montage_id: int = 0,
    runs=_RUNS,
    run_numbers: list[int] | None = None,
    domain: str = "neural",
    experimenter: str = "jw",
    subject_details: dict | None = None,
    probes: list[dict] | None = None,
) -> JobRequest:
    """A `JobRequest` naming `(montage_id, session_datetime)` in its
    selection and asserting `runs`, with `run_numbers` present only when the
    caller supplies one -- an absent key and an empty list are both legal
    ways to ask for a canonical activation, and callers exercising that
    distinction build the dict directly rather than through this helper. A
    canonical's probes each sort no run unless a test says otherwise.
    """
    selection: dict = {"session_datetime": session_datetime, "montage_id": montage_id}
    if run_numbers is not None:
        selection["run_numbers"] = run_numbers
    elif probes:
        selection["probe_runs"] = {probe["serial"]: [] for probe in probes}
    return JobRequest(
        domain=domain,
        selection=selection,
        parameters={},
        idempotency_key=idempotency_key,
        metadata=MetadataBundle(
            runs=_asserted(runs),
            probes=probes or [],
            experimenter=experimenter,
            subject=subject,
            task_types=[],
            subject_details=subject_details,
        ),
    )


def test_accept_is_idempotent_on_the_same_key(landed_session, prefix):
    """A resubmission of the identical `JobRequest` (the same idempotency
    key, the same everything) must return the same Activation and must not
    duplicate the Montage/RunAssertion rows or the Request row.

    The selection's `session_datetime` is timezone-aware here on purpose,
    not merely for realism: DataJoint's blob codec drops a datetime's tzinfo
    on its first round trip through the database (confirmed directly against
    this project's installed datajoint -- pack/unpack an aware value and it
    comes back naive), so a payload holding the raw, still-aware value on a
    SECOND call would compare unequal to the first call's now-naive stored
    copy and `_reject_key_reuse` would refuse this exact retry as key reuse.
    `accept()` avoids this by never storing a live datetime object in the
    payload at all -- `model_dump(mode="json")` renders it to a
    deterministic ISO-8601 string first (review round 1, I1), and a string
    carries no tzinfo for the blob codec to drop in the first place. This
    test fails loudly (a `DataJointError` mentioning "payload") if that
    normalisation is ever removed.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    subject = "jbidm01"
    naive_dt = datetime.datetime(2027, 5, 2, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbidm01-k1",
    )

    first = accept(job, prefix=prefix)
    second = accept(job, prefix=prefix)  # the identical JobRequest, resubmitted

    assert first == second
    assert len(schema_request.Request & {"idempotency_key": "jbidm01-k1"}) == 1
    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(core.Montage & {**session_key, "montage_id": 0}) == 1
    assert len(core.RunAssertion & {**session_key, "run_number": 1}) == 1


def test_no_run_numbers_is_canonical_and_some_run_numbers_is_derivative(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbcd001"
    naive_dt = datetime.datetime(2027, 5, 5, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])

    canonical_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbcd001-k1",
    )
    derivative_job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbcd001-k2",
        run_numbers=[1, 2],
    )

    canonical_key = accept(canonical_job, prefix=prefix)
    derivative_key = accept(derivative_job, prefix=prefix)

    assert canonical_key["activation_id"] == 0
    assert (schema_request.Activation & canonical_key).fetch1("role") == "canonical"
    assert derivative_key["activation_id"] != 0
    assert (schema_request.Activation & derivative_key).fetch1("role") == "derivative"
    assert len(schema_request.ActivationRun & derivative_key) == 2


def test_accept_treats_the_montage_window_as_half_open(landed_session, prefix):
    """[start_s, end_s) -- a run starting inside the montage is the file's
    even when it ends exactly at the montage's end; a run starting exactly
    where the montage ends is not the file's at all. Boundary conditions are
    exactly where an off-by-one in the comparison would hide."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbedg01"
    naive_dt = datetime.datetime(2027, 5, 10, 9, 0)
    runs = [(1, 0.0, 4.0), (2, 8.0, 12.0), (3, 12.0, 16.0)]
    landed_session(subject, naive_dt, runs=runs, montages=[_M0])

    canonical = accept(_request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                                idempotency_key="jbedg01-k1", runs=runs),
                       prefix=prefix)
    assert sorted(int(n) for n in (schema_request.ActivationRun & canonical).to_arrays("run_number")) == [1, 2]

    touching_job = _request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                            idempotency_key="jbedg01-k2", runs=runs,
                            run_numbers=[3])
    with pytest.raises(ValueError, match=r"run\(s\) \[3\] outside montage 0's window"):
        accept(touching_job, prefix=prefix)


def test_accept_rejects_a_selection_missing_a_required_key(landed_session, prefix):
    subject = "jbkey01"
    naive_dt = datetime.datetime(2027, 5, 11, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])

    job = JobRequest(
        domain="neural",
        selection={"montage_id": 0},  # session_datetime missing
        parameters={},
        idempotency_key="jbkey01-k1",
        metadata=MetadataBundle(
            runs=_asserted(),
            probes=[],
            experimenter="jw",
            subject=subject,
            task_types=[],
        ),
    )

    from wl_preproc.responder.jobs import accept

    with pytest.raises(ValueError, match="session_datetime"):
        accept(job, prefix=prefix)


def test_accept_rejects_when_no_montage_is_on_record(landed_session, prefix):
    """A montage this host has not listed is refused, never created from the
    request (design spec `2026-11-24-montages-design.md` section 4.6)."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbnomt1"
    naive_dt = datetime.datetime(2027, 5, 12, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=3,
        idempotency_key="jbnomt1-k1",
    )

    with pytest.raises(ValueError, match="no montage 3 is listed"):
        accept(job, prefix=prefix)

    assert len(schema_request.Request & {"idempotency_key": "jbnomt1-k1"}) == 0


def test_accept_rejects_an_oversized_subject(dj_conn, prefix):
    from wl_preproc.ingest import landing
    from wl_preproc.responder.jobs import accept

    long_subject = "s" * (landing.SUBJECT_MAX_LEN + 1)
    job = _request(
        subject=long_subject,
        session_datetime=datetime.datetime(2027, 5, 13, 9, 0, tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbovr01-k1",
    )

    with pytest.raises(ValueError, match="subject"):
        accept(job, prefix=prefix)


def test_accept_refuses_to_run_inside_a_transaction(landed_session, prefix):
    """Correction 1: `accept()` opens no transaction of its own, so a caller
    that wraps it in one is what makes `submit()`'s own no-nesting guard
    fire -- mirroring `tests/schema/test_request.py::
    test_submit_refuses_to_run_inside_a_transaction`.

    Nothing of the request lands: its run ids are written inside
    `submit()`'s own transaction, which its guard refuses to open here, so a
    caller who makes this mistake and then retries `accept()` correctly,
    outside any transaction, writes them then. *It said, until the
    leftovers' review moved the Montage write into `submit()`, that the
    Montage row still landed here, in the caller's transaction; true when
    written. A request writes no `Montage` at all since the montages piece
    (design spec `2026-11-24-montages-design.md` section 4.6).*
    """
    import datajoint as dj

    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    subject = "jbtxn01"
    naive_dt = datetime.datetime(2027, 5, 6, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbtxn01-k1",
    )

    conn = dj.conn()
    with conn.transaction:
        with pytest.raises(dj.DataJointError, match="do not nest"):
            accept(job, prefix=prefix)

    assert len(schema_request.Request & {"idempotency_key": "jbtxn01-k1"}) == 0
    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(core.RunAssertion & session_key) == 0


def test_session_datetime_is_normalised_through_to_naive_utc(landed_session, prefix):
    """`landing.to_naive_utc` is the one conversion every other datetime key
    in this codebase goes through. A non-UTC offset proves the actual
    wall-clock conversion rather than merely a value that was already UTC
    and would pass even with tzinfo dropped naively.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbtz001"
    naive_dt = datetime.datetime(2027, 5, 7, 14, 0)  # what must actually get stored
    landed_session(subject, naive_dt, montages=[_M0])

    # 09:00 at a fixed UTC-05:00 offset is the identical instant as 14:00 UTC.
    offset_aware = datetime.datetime(
        2027, 5, 7, 9, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5))
    )
    job = _request(
        subject=subject,
        session_datetime=offset_aware,
        montage_id=0,
        idempotency_key="jbtz001-k1",
    )

    key = accept(job, prefix=prefix)

    assert key["session_datetime"] == naive_dt
    assert len(schema_request.Activation & key) == 1


# --- Review round 1 (2026-08-16): two Criticals and two Importants, all in
# territory the original 12 tests above could not reach. ---


def test_accept_coerces_an_iso8601_string_session_datetime(landed_session, prefix):
    """C2: real wire traffic carries `session_datetime` as a plain `str` --
    JSON has no datetime type, and `docs/schemas/job_request.json` declares
    `selection` as a bare object with no `session_datetime` property, so
    pydantic coerces nothing inside it. Every other test in this file hands
    `accept()` a live `datetime.datetime` directly; this is the one that
    proves the actual wire shape works too.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbstr01"
    naive_dt = datetime.datetime(2027, 5, 15, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])
    job = _request(
        subject=subject,
        session_datetime="2027-05-15T09:00:00Z",  # a plain string, not a datetime
        montage_id=0,
        idempotency_key="jbstr01-k1",
    )

    key = accept(job, prefix=prefix)

    assert key["session_datetime"] == naive_dt
    assert len(schema_request.Activation & key) == 1


def test_accept_rejects_a_session_datetime_that_is_neither_a_datetime_nor_a_string(
    landed_session, prefix
):
    """Before C2's fix this reached `to_naive_utc` and raised a bare
    `AttributeError` -- outside `accept()`'s documented `ValueError`
    contract, and something Task 8's handler never expected from a request
    that validated cleanly against `JobRequest`'s own schema."""
    from wl_preproc.responder.jobs import accept

    subject = "jbbad01"
    naive_dt = datetime.datetime(2027, 5, 16, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])
    job = _request(
        subject=subject,
        session_datetime=12345,  # neither a datetime nor a string
        montage_id=0,
        idempotency_key="jbbad01-k1",
    )

    with pytest.raises(ValueError, match="session_datetime"):
        accept(job, prefix=prefix)


def test_accept_normalises_an_aware_datetime_anywhere_in_the_stored_payload(
    landed_session, prefix
):
    """I1: the tzinfo-drop finding is not scoped to `selection.
    session_datetime` -- any aware datetime anywhere in the stored payload
    reproduces the identical defect on a retry. Here it's nested inside
    `parameters` instead, a field `accept()` never reads for its own logic
    but still stores verbatim as evidence.
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    subject = "jbpar01"
    naive_dt = datetime.datetime(2027, 5, 23, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])
    aware_param = datetime.datetime(2027, 5, 23, 8, 0, tzinfo=datetime.UTC)
    job = JobRequest(
        domain="neural",
        selection={"session_datetime": naive_dt.replace(tzinfo=datetime.UTC), "montage_id": 0},
        parameters={"calibrated_on": aware_param},
        idempotency_key="jbpar01-k1",
        metadata=MetadataBundle(
            runs=_asserted(),
            probes=[],
            experimenter="jw",
            subject=subject,
            task_types=[],
        ),
    )

    first = accept(job, prefix=prefix)
    second = accept(job, prefix=prefix)  # identical resubmission -- must not raise

    assert first == second
    assert len(schema_request.Request & {"idempotency_key": "jbpar01-k1"}) == 1


def test_an_out_of_range_montage_id_is_refused_with_the_field_named(landed_session, prefix):
    """I2: `core.Montage.montage_id` is a signed `tinyint` (-128..127), and a
    selection naming one it cannot hold is refused with the field named,
    before anything reads the database for it. *Its boundary case went with
    `metadata.montage_boundaries` (design spec `2026-11-24-montages-design.md`
    section 4.6): a request no longer sends a montage's bounds.*
    """
    from wl_preproc.responder.jobs import accept

    subject = "jbrng01"
    naive_dt = datetime.datetime(2027, 5, 17, 9, 0)
    landed_session(subject, naive_dt)
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=99999,
        idempotency_key="jbrng01-k1",
    )

    with pytest.raises(ValueError, match=re.escape("selection['montage_id']=99999 is outside the column's range")):
        accept(job, prefix=prefix)


def test_accept_rejects_a_session_this_host_has_never_ingested(
    dj_conn, prefix, table_snapshot, deep_equal
):
    """Whole-branch review C1. A job for a `(subject, session_datetime)` with
    no `Session` row is a `ValueError` -> `422`, raised BEFORE any write --
    not an `IntegrityError` out of `core.Montage.insert`'s foreign key, which
    `handler.py` maps to a `500` and `docs/ops/lab-host-protocol.md`
    documents as retryable. A conforming client retried that forever.

    **This is the ordinary case.** wl.works knows a session exists from the
    ELN hours before its transfer lands here, so any button pressed in that
    window arrives at exactly this state. Deliberately NOT using
    `landed_session`: the whole point is that nothing has landed. `dj_conn`
    and `prefix` are taken directly instead, and `accept()` activates the
    schema itself.

    **Proven by row snapshot, not by `in_transaction`.** DataJoint's
    `insert()` calls `connection.query()` directly and never touches the
    transaction machinery, so `in_transaction` reads `False` for a writing
    function and a reading one alike -- this codebase has been misled by
    that assumption four separate times. Every table `accept()` can write,
    plus `Session` itself (which it must never create), is snapshotted
    whole -- every row, every column -- before and after, and compared with
    `deep_equal` rather than bare `==` (see `tests/conftest.py`).

    Both halves of the message are asserted, because both are what makes the
    422 body actionable for wl.works' UI: the subject and the session
    datetime name WHICH session, and "not yet on record" is what lets them
    render "that session has not arrived yet" instead of "invalid request".
    """
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core, pipeline
    from wl_preproc.schema import request as schema_request

    schema_request.activate(prefix=prefix)

    subject = "jbnoses"
    naive_dt = datetime.datetime(2027, 5, 25, 9, 0)
    session_key = {"subject": subject, "session_datetime": naive_dt}
    assert len(pipeline.Session & session_key) == 0, "the premise: this session never landed"

    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        montage_id=0,
        idempotency_key="jbnoses-k1",
    )

    written_tables = [
        core.Montage,
        core.RunAssertion,
        schema_request.Request,
        schema_request.Activation,
        schema_request.ActivationRun,
        schema_request.ActivationProbeRun,
        pipeline.Session,
    ]
    before = [table_snapshot(table) for table in written_tables]

    with pytest.raises(ValueError) as excinfo:
        accept(job, prefix=prefix)

    message = str(excinfo.value)
    assert subject in message, "the message must name which subject"
    assert "2027-05-25T09:00:00" in message, "the message must name which session"
    assert "not yet on record" in message
    assert "IntegrityError" not in message and "foreign key" not in message, (
        "the database's own constraint text must never be what the caller reads"
    )

    after = [table_snapshot(table) for table in written_tables]
    for table, rows_before, rows_after in zip(written_tables, before, after, strict=True):
        assert deep_equal(rows_before, rows_after), (
            f"{table.__name__} changed under a rejected request: "
            f"{len(rows_before)} row(s) before, {len(rows_after)} after"
        )


def test_the_subjects_details_fill_its_own_record(landed_session, prefix):
    """Design spec `2026-09-28-nwb-builder-design.md` section 9, the
    requester's decision: wl.works sends the animal's species, sex and date
    of birth with the job request, and they replace what wl-preproc knew --
    `ingest/landing.py` lands only a stub."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import pipeline

    subject = "jbsubj01"
    naive_dt = datetime.datetime(2027, 5, 9, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        idempotency_key="jbsubj01-k1",
        subject_details={"species": "Macaca mulatta", "sex": "F",
                         "date_of_birth": datetime.date(2016, 3, 2)},
    )

    accept(job, prefix=prefix)

    row = (pipeline.subject.Subject & {"subject": subject}).fetch1()
    assert (row["sex"], row["subject_birth_date"]) == ("F", datetime.date(2016, 3, 2))
    assert (pipeline.subject.Subject.Species & {"subject": subject}).fetch1("species") == "Macaca mulatta"


def test_a_nickname_is_stored_and_kept_when_absent(landed_session, prefix):
    """wl.works' nickname for the animal lands in `Subject.subject_nickname`;
    a later request without one leaves it (the latest request that carries it
    wins)."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import pipeline

    subject = "jbnick01"
    naive_dt = datetime.datetime(2027, 5, 11, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])
    accept(_request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                    idempotency_key="jbnick01-k1",
                    subject_details={"sex": "F", "nickname": "Pico"}), prefix=prefix)
    assert (pipeline.subject.Subject & {"subject": subject}).fetch1("subject_nickname") == "Pico"

    accept(_request(subject=subject, session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                    idempotency_key="jbnick01-k2",
                    subject_details={"sex": "F"}), prefix=prefix)
    assert (pipeline.subject.Subject & {"subject": subject}).fetch1("subject_nickname") == "Pico"


def test_a_request_without_details_leaves_the_subject_alone(landed_session, prefix):
    """They are optional: a request that does not carry them changes nothing
    about the subject."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import pipeline

    subject = "jbsubj02"
    naive_dt = datetime.datetime(2027, 5, 10, 9, 0)
    landed_session(subject, naive_dt, montages=[_M0])
    before = (pipeline.subject.Subject & {"subject": subject}).fetch1()
    job = _request(
        subject=subject,
        session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
        idempotency_key="jbsubj02-k1",
    )

    accept(job, prefix=prefix)

    assert (pipeline.subject.Subject & {"subject": subject}).fetch1() == before
    assert len(pipeline.subject.Subject.Species & {"subject": subject}) == 0


# -- What wl.works reports about each insertion (design spec
# 2026-09-30-nwb-probes-design.md section 2.2)

def _probe(insertion_number, serial, **fields):
    return {"serial": serial, "insertion_number": insertion_number, **fields}


def test_every_insertion_wl_works_reports_is_recorded(landed_session, prefix):
    """The aim, the trajectory and the latest assignment, per insertion, as
    wl.works sent them. The serial is plain text: the probe may not have
    been recorded yet."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import ephys

    naive_dt = datetime.datetime(2027, 5, 11, 9, 0)
    key = landed_session("jbprob01", naive_dt, montages=[_M0])
    accept(_request(
        subject="jbprob01", session_datetime=naive_dt.replace(tzinfo=datetime.UTC), idempotency_key="jbprob01-k1",
        probes=[
            _probe(1, "19011110001", trajectory_id="T-7", target={"area": "V4d", "atlas": "CHARM", "atlas_level": 6},
                   area_assignment={"area": "V4v", "source": "at_rig",
                                    "asserted_at": datetime.datetime(2027, 5, 11, 10, 0, 0, 250000,
                                                                     tzinfo=datetime.UTC)}),
            _probe(2, "19011110002"),
        ],
    ), prefix=prefix)

    reports = (ephys.InsertionReport & key).to_dicts(order_by="insertion_number")
    assert [(r["insertion_number"], r["probe_serial"], r["trajectory_id"], r["target_area"], r["target_atlas"],
             r["target_atlas_level"]) for r in reports] == [
        (1, "19011110001", "T-7", "V4d", "CHARM", 6), (2, "19011110002", None, None, None, None)]
    (assigned,) = (ephys.AreaAssignment & key).to_dicts()
    assert (assigned["insertion_number"], assigned["area"], assigned["source"]) == (1, "V4v", "at_rig")
    # Naive UTC, to the microsecond: two assignments a second apart stay two.
    assert assigned["asserted_at"] == datetime.datetime(2027, 5, 11, 10, 0, 0, 250000)


def test_a_later_request_corrects_the_report_and_adds_the_assignment(landed_session, prefix):
    """The latest request wins for the report, as for the subject's details;
    assignments are append-only, as wl.works' own table is. An insertion a
    later request does not mention is left alone: a request may name only
    its own montage's insertions."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import ephys

    naive_dt = datetime.datetime(2027, 5, 12, 9, 0)
    runs = [*_RUNS, (4, 25.0, 30.0)]  # one run in each of the three montages
    key = landed_session("jbprob02", naive_dt, runs=runs, montages=[
        {"montage_id": m, "start_s": 12.0 * m, "end_s": 12.0 * (m + 1)} for m in range(3)])
    first = {"area": "V4d", "source": "at_rig", "asserted_at": "2027-05-12T10:00:00Z"}
    for idempotency_key, montage_id, probes in (
        ("jbprob02-k1", 0, [_probe(1, "19011110001", area_assignment=first), _probe(2, "19011110002")]),
        ("jbprob02-k2", 1, [_probe(1, "19011110003", trajectory_id="T-8", area_assignment={
            "area": "V4v", "source": "histology", "asserted_at": "2027-06-01T12:00:00Z"})]),
        # A retry of the first: the report regresses, as the subject's details
        # would, and its assignment is not recorded twice.
        ("jbprob02-k1", 0, [_probe(1, "19011110001", area_assignment=first), _probe(2, "19011110002")]),
        ("jbprob02-k3", 2, [_probe(1, "19011110003", trajectory_id="T-8")]),
    ):
        accept(_request(subject="jbprob02", session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                        idempotency_key=idempotency_key, montage_id=montage_id, runs=runs, probes=probes),
               prefix=prefix)

    reports = (ephys.InsertionReport & key).to_dicts(order_by="insertion_number")
    assert [(r["insertion_number"], r["probe_serial"], r["trajectory_id"]) for r in reports] == [
        (1, "19011110003", "T-8"), (2, "19011110002", None)]
    assigned = (ephys.AreaAssignment & key).to_dicts(order_by="asserted_at")
    assert [(a["area"], a["source"]) for a in assigned] == [("V4d", "at_rig"), ("V4v", "histology")]


def test_a_refused_request_records_no_report(landed_session, prefix):
    """Every check runs before anything is written (review C1): a request
    refused for its montage leaves no report behind."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import ephys

    naive_dt = datetime.datetime(2027, 5, 13, 9, 0)
    key = landed_session("jbprob03", naive_dt)
    with pytest.raises(ValueError, match="no montage 4 is listed"):
        accept(_request(subject="jbprob03", session_datetime=naive_dt.replace(tzinfo=datetime.UTC),
                        idempotency_key="jbprob03-k1", montage_id=4, probes=[_probe(1, "19011110001")]),
               prefix=prefix)
    ephys.activate(prefix=prefix)
    assert not ephys.InsertionReport & key


# -- The canonical lifecycle (design spec 2026-09-30-canonical-lifecycle-design.md section 3)

def _lifecycle_job(subject, session_datetime, key, **selection) -> JobRequest:
    return JobRequest(
        domain="neural",
        selection={"session_datetime": session_datetime, "montage_id": 0, **selection},
        parameters={},
        idempotency_key=key,
        metadata=MetadataBundle(runs=_asserted(), probes=[], experimenter="jw", subject=subject, task_types=[]),
    )


def test_a_replacement_request_supersedes_the_named_canonical(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 5, 20, 10, 0)
    landed_session("jblc002", when, montages=[_M0])
    first = accept(_lifecycle_job("jblc002", when, "jblc002-k1"), prefix=prefix)
    job = _lifecycle_job("jblc002", when, "jblc002-k2", role="canonical", supersedes_activation_id=0)
    replacement = accept(job, prefix=prefix)
    row = (schema_request.Activation & replacement).fetch1()
    assert (row["activation_id"], row["role"], row["supersedes"]) == (1, "canonical", first["activation_id"])
    assert accept(job, prefix=prefix) == replacement
    assert accept(_lifecycle_job("jblc002", when, "jblc002-k3"), prefix=prefix) == replacement
    # The first request, re-sent under its own key (the 2b final review's M2).
    assert accept(_lifecycle_job("jblc002", when, "jblc002-k1"), prefix=prefix) == replacement


def test_a_replacement_of_a_superseded_canonical_is_a_conflict(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema.request import SupersedeConflict

    when = datetime.datetime(2027, 5, 20, 11, 0)
    landed_session("jblc003", when, montages=[_M0])
    accept(_lifecycle_job("jblc003", when, "jblc003-k1"), prefix=prefix)
    accept(_lifecycle_job("jblc003", when, "jblc003-k2", role="canonical", supersedes_activation_id=0), prefix=prefix)
    with pytest.raises(SupersedeConflict, match="current canonical is activation 1"):
        accept(_lifecycle_job("jblc003", when, "jblc003-k3", role="canonical", supersedes_activation_id=0),
               prefix=prefix)


@pytest.mark.parametrize("selection", [
    {"supersedes_activation_id": 0},
    {"role": "derivative", "supersedes_activation_id": 0, "run_numbers": [1]},
    {"role": "derivative"},
    {"role": "bogus"},
    {"role": "canonical", "supersedes_activation_id": -1},
    {"role": "canonical", "supersedes_activation_id": True},
    {"role": "canonical", "supersedes_activation_id": "0"},
    {"role": "canonical", "run_numbers": [1]},
    {"run_numbers": [3]},
])
def test_a_selection_the_lifecycle_refuses_is_a_value_error(landed_session, prefix, selection):
    """Section 3's refusals, each a `422` over HTTP: only a canonical
    supersedes, a derivative names its runs, and an activation id is one
    non-negative integer."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 5, 20, 12, 0)
    landed_session("jblc004", when, montages=[_M0])
    key = f"jblc004-{sorted(selection.items())!r}"
    with pytest.raises(ValueError):
        accept(_lifecycle_job("jblc004", when, key, **selection), prefix=prefix)
    assert not schema_request.Request & {"idempotency_key": key}


def test_a_canonical_role_with_no_runs_named_takes_every_run_of_the_montage(landed_session, prefix):
    """`role: canonical` with an empty `run_numbers` is the plain canonical:
    the file holds every measured run of its montage (the requester's
    decision 1 of 2026-10-01)."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 5, 20, 13, 0)
    landed_session("jblc005", when, montages=[_M0])
    key = accept(_lifecycle_job("jblc005", when, "jblc005-k1", role="canonical", run_numbers=[]), prefix=prefix)
    assert key["activation_id"] == 0 and (schema_request.Activation & key).fetch1("role") == "canonical"
    assert sorted(int(n) for n in (schema_request.ActivationRun & key).to_arrays("run_number")) == [1, 2]


@pytest.mark.parametrize("selection", [{"supersedes_activation_id": None},
                                       {"role": "canonical", "supersedes_activation_id": None}])
def test_a_null_supersedes_is_absent(landed_session, prefix, selection):
    """The 2b final review's M10: a client that serialises an optional
    field as null asks for a plain canonical, as a null `role` already
    does."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 5, 20, 14, 0)
    landed_session("jblc006", when, montages=[_M0])
    key = accept(_lifecycle_job("jblc006", when, f"jblc006-{len(selection)}", **selection), prefix=prefix)
    row = (schema_request.Activation & key).fetch1()
    assert (row["activation_id"], row["role"], row["supersedes"]) == (0, "canonical", None)


# -- Requests that name runs (design spec
# `2026-10-01-session-listing-and-run-requests-design.md` section 3), with one
# probe whose runs a canonical states.


def _landed_with_runs(landed_session, subject: str, day: int, runs=_RUNS, montages=(_M0,)) -> dict:
    return landed_session(subject, datetime.datetime(2027, 9, day, 9, 0), runs=runs, montages=montages)


def _runs_job(key: dict, idempotency_key: str, *, runs=_RUNS, ids: dict | None = None,
              serials=(_SERIAL,), probes: list[dict] | None = None, **selection) -> JobRequest:
    """Montage 0's request, asserting `runs`; its probes are `serials` at insertions 1, 2, ..., or `probes`."""
    if probes is None:
        probes = [{"serial": serial, "insertion_number": index} for index, serial in enumerate(serials, start=1)]
    return JobRequest(
        domain="neural",
        selection={"session_datetime": key["session_datetime"], "montage_id": 0, **selection},
        parameters={},
        idempotency_key=idempotency_key,
        metadata=MetadataBundle(runs=_asserted(runs, ids), probes=probes, experimenter="jw",
                                subject=key["subject"], task_types=[]),
    )


def _run_rows(table, key) -> list:
    return sorted(tuple(row[name] for name in ("probe_serial", "run_number") if name in row)
                  for row in (table & key).to_dicts())


def test_a_canonical_naming_runs_records_them_and_holds_its_montages_runs(landed_session, prefix):
    """The requester's decision 1: the file holds every measured run whose
    start lies in the montage's window; each probe's list is its sort's."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob1", 1)
    activation = accept(_runs_job(key, "runjob1-k1", probe_runs={_SERIAL: [1]}), prefix=prefix)
    assert sorted((row["run_number"], row["works_run_id"]) for row in (core.RunAssertion & key).to_dicts()) == [
        (1, "wr-1"), (2, "wr-2"), (3, "wr-3")]
    assert _run_rows(schema_request.ActivationRun, activation) == [(1,), (2,)]
    assert _run_rows(schema_request.ActivationProbeRun, activation) == [(_SERIAL, 1)]


@pytest.mark.parametrize("runs, expect", [
    ([(1, 0.01, 4.0), (2, 5.0, 11.0)], "run 1's start, 0.01 s, is not the measured 0.0 s"),
    ([(1, 0.0, 4.0), (2, 5.0, 11.5)], "run 2's end, 11.5 s, is not the measured 11.0 s"),
    ([(1, 0.0, 4.0), (2, 5.0, 11.0), (9, 20.0, 21.0)], "run 9 is not a measured run of this session"),
    ([(1, 0.0, 4.0)], "measured run(s) [2] lie in montage 0's window"),
])
def test_a_request_from_a_stale_listing_is_a_run_mismatch_and_writes_nothing(landed_session, prefix, runs, expect):
    """Checked as the request arrives, against `core.Run`, within about 2 ms
    (design spec section 3.2): the reason names the run and says to rebuild."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob2", 2)
    with pytest.raises(ValueError, match=re.escape(expect)) as refused:
        accept(_runs_job(key, f"runjob2-{len(runs)}-{runs[-1][2]}", runs=runs, probe_runs={_SERIAL: [1]}),
               prefix=prefix)
    assert "rebuild the request from a fresh GET /sessions" in str(refused.value)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (0, 0)


@pytest.mark.parametrize("subject, measured, runs, selection, expect", [
    # Which copy, and which id, is wl.works'? Neither is guessed.
    ("runjob10", _RUNS, _RUNS + [(1, 0.0, 4.0)], {"probe_runs": {_SERIAL: [1]}},
     "metadata.runs names run 1 twice"),
    # A canonical holds every measured run of its window, and this one has none.
    ("runjob11", [(3, 13.0, 16.0)], [(3, 13.0, 16.0)], {"probe_runs": {_SERIAL: []}},
     "montage 0's window [0.0, 12.0) holds no measured run"),
    # Amendment 19: a derivative's run is asserted now or before; without that
    # its file's run would carry no works_run_id to join.
    ("runjob12", _RUNS, [(1, 0.0, 4.0), (3, 13.0, 16.0)], {"run_numbers": [2]},
     "selection names run(s) [2] asserted neither in metadata.runs nor before"),
    ("runjob13", _RUNS, _RUNS, {"run_numbers": [9]},
     "selection names run(s) [9] are not measured runs of this session"),
])
def test_runs_that_cannot_be_held_as_asserted_are_refused_and_write_nothing(landed_session, prefix, subject,
                                                                              measured, runs, selection, expect):
    """Design spec section 3.2's refusals the stale-listing cases above do not
    reach (Plan B's final review, I2): each is a 422 before anything is
    written."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, subject, 10, runs=measured)
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, f"{subject}-k1", runs=runs, **selection), prefix=prefix)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (0, 0)


def test_a_run_asserted_again_under_another_id_is_a_conflict(landed_session, prefix):
    """A 409, which wl.works stops on: the two disagree about which run this
    is. Through the server's translation, as wl.works meets it."""
    from wl_preproc.responder.handler import ConflictError
    from wl_preproc.responder.jobs import RunIdConflict, accept
    from wl_preproc.responder.server import _translate_accept_errors

    key = _landed_with_runs(landed_session, "runjob3", 3)
    accept(_runs_job(key, "runjob3-k1", probe_runs={_SERIAL: [1, 2]}), prefix=prefix)
    renamed = _runs_job(key, "runjob3-k2", ids={2: "wr-other"}, probe_runs={_SERIAL: [1, 2]})
    with pytest.raises(RunIdConflict, match="run 2 is recorded with works_run_id 'wr-2'; this request names 'wr-other'"):
        accept(renamed, prefix=prefix)
    with pytest.raises(ConflictError):
        _translate_accept_errors(renamed, prefix=prefix)


def test_one_works_run_id_for_two_runs_is_refused(landed_session, prefix):
    """An `animal_session_run` records the one measured run it came from, so
    one id naming two runs would join two of the file's runs to one row in
    wl.works (Plan B's final review, M2). In one request it is a 422; against
    an id already recorded for another run of the session, a 409, as for a
    run named under a second id. Neither writes anything."""
    from wl_preproc.responder.jobs import RunIdConflict, accept
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob14", 14)
    with pytest.raises(ValueError, match=re.escape("metadata.runs names works_run_id 'wr-x' for runs 1 and 2")):
        accept(_runs_job(key, "runjob14-k1", ids={1: "wr-x", 2: "wr-x"}, probe_runs={_SERIAL: [1]}), prefix=prefix)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (0, 0)

    accept(_runs_job(key, "runjob14-k2", runs=_RUNS[:2], probe_runs={_SERIAL: [1, 2]}), prefix=prefix)
    before = len(schema_request.Activation & key)
    reused = _runs_job(key, "runjob14-k3", runs=_RUNS[2:], ids={3: "wr-1"}, run_numbers=[2])
    with pytest.raises(RunIdConflict, match=re.escape("works_run_id 'wr-1' is recorded for run 1; this request "
                                                      "names it for run 3")):
        accept(reused, prefix=prefix)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (2, before)


@pytest.mark.parametrize("refusal", ["key_reuse", "supersede"])
def test_a_request_refused_with_a_409_records_none_of_its_runs(landed_session, prefix, refusal):
    """A refused request leaves no `RunAssertion` behind (the plan's Global
    Constraint; Plan B's final review, M1, and the leftovers' review): it is
    recorded in the same transaction as its `Request` and `Activation`, so a
    409 from `submit*()` rolls it back with them. *It held for the request's
    `Montage` rows too, until a request stopped sending them (design spec
    `2026-11-24-montages-design.md` section 4.6).*"""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core
    from wl_preproc.schema.request import KeyReuseError, SupersedeConflict

    subject = {"key_reuse": "runjob15", "supersede": "runjob16"}[refusal]
    key = _landed_with_runs(landed_session, subject, 15)
    accept(_runs_job(key, f"{subject}-k1", runs=_RUNS[:2], probe_runs={_SERIAL: [1, 2]}), prefix=prefix)
    if refusal == "key_reuse":
        refused, raised = _runs_job(key, f"{subject}-k1", probe_runs={_SERIAL: [1, 2]}), KeyReuseError
    else:
        refused = _runs_job(key, f"{subject}-k2", role="canonical", supersedes_activation_id=7,
                            probe_runs={_SERIAL: [1, 2]})
        raised = SupersedeConflict
    with pytest.raises(raised):
        accept(refused, prefix=prefix)
    assert sorted(int(n) for n in (core.RunAssertion & key).to_arrays("run_number")) == [1, 2]


@pytest.mark.parametrize("path", ["replacement", "canonical_dedupe", "derivative_dedupe"])
def test_every_accepted_path_records_a_run_first_asserted_on_it(landed_session, prefix, path):
    """`submit*()` records a request's run ids on each of its success paths
    (Plan B's final review, M1): a replacement, a second canonical under a
    new key, and a repeated derivative, each the first request to assert run
    3. A path that skipped it would refuse a later derivative of run 3 as
    "asserted neither in metadata.runs nor before" (the leftovers' review)."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    subject = {"replacement": "runjob17", "canonical_dedupe": "runjob18", "derivative_dedupe": "runjob19"}[path]
    key = _landed_with_runs(landed_session, subject, 17)
    accept(_runs_job(key, f"{subject}-k1", runs=_RUNS[:2], probe_runs={_SERIAL: [1, 2]}), prefix=prefix)
    if path == "derivative_dedupe":
        accept(_runs_job(key, f"{subject}-k2", runs=_RUNS[:2], run_numbers=[1]), prefix=prefix)
        again = _runs_job(key, f"{subject}-k3", run_numbers=[1])
    elif path == "replacement":
        again = _runs_job(key, f"{subject}-k3", role="canonical", supersedes_activation_id=0,
                          probe_runs={_SERIAL: [1, 2]})
    else:
        again = _runs_job(key, f"{subject}-k3", probe_runs={_SERIAL: [1, 2]})
    accept(again, prefix=prefix)
    assert sorted(int(n) for n in (core.RunAssertion & key).to_arrays("run_number")) == [1, 2, 3]


def test_runs_not_yet_measured_are_not_yet_ingested(landed_session, prefix):
    """wl.works retries this one: the event stage has not read the session."""
    from wl_preproc.responder.jobs import accept

    key = landed_session("runjob4", datetime.datetime(2027, 9, 4, 9, 0), runs=(), montages=[_M0])
    with pytest.raises(ValueError, match="has no measured run on this host yet"):
        accept(_runs_job(key, "runjob4-k1", probe_runs={_SERIAL: [1]}), prefix=prefix)


@pytest.mark.parametrize("probe_runs, expect", [
    (None, "selection.probe_runs must give every probe's runs"),
    ({}, "selection.probe_runs names [], and metadata.probes names ['19011110001']"),
    ({_SERIAL: [1], "19011110002": [1]}, "selection.probe_runs names ['19011110001', '19011110002']"),
    ({_SERIAL: [3]}, "probe 19011110001's runs [3] are not among the file's runs [1, 2]"),
    ({_SERIAL: ["1"]}, "selection.probe_runs['19011110001'] must be a list of run numbers"),
])
def test_each_probes_runs_are_stated_in_full_and_within_the_file(landed_session, prefix, probe_runs, expect):
    """wl.works' Plan 20 rule: the record is the run set each probe resolved
    to, so every probe's list is stated, and only the file's runs."""
    from wl_preproc.responder.jobs import accept

    key = _landed_with_runs(landed_session, "runjob5", 5)
    selection = {} if probe_runs is None else {"probe_runs": probe_runs}
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, f"runjob5-{expect[:20]}", **selection), prefix=prefix)


def test_a_derivative_names_its_runs(landed_session, prefix):
    """The requester's decision 3: a derivative selects whole runs, the unit
    a sort runs over, so each probe the request names covers them all. With
    no row, the sorter and the description would read that probe as sorting
    nothing (Plan B's final review, I1)."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob6", 6)
    activation = accept(_runs_job(key, "runjob6-k1", run_numbers=[2], serials=(_SERIAL, "19011110002")),
                        prefix=prefix)
    assert (schema_request.Activation & activation).fetch1("role") == "derivative"
    assert _run_rows(schema_request.ActivationRun, activation) == [(2,)]
    assert _run_rows(schema_request.ActivationProbeRun, activation) == [(_SERIAL, 2), ("19011110002", 2)]


@pytest.mark.parametrize("selection, expect", [
    ({"role": "canonical", "run_numbers": [1], "probe_runs": {_SERIAL: [1]}}, "selection.run_numbers is a derivative's"),
    ({"run_numbers": [2], "probe_runs": {_SERIAL: [2]}}, "selection.probe_runs is a canonical's"),
    ({"run_numbers": ["2"]}, "selection.run_numbers must be a list of run numbers"),
    ({"run_numbers": [3]}, "selection names run(s) [3] outside montage 0's window"),
])
def test_a_selection_that_misplaces_runs_is_refused(landed_session, prefix, selection, expect):
    from wl_preproc.responder.jobs import accept

    key = _landed_with_runs(landed_session, "runjob7", 7)
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, f"runjob7-{expect[:24]}", **selection), prefix=prefix)


@pytest.mark.parametrize("selection, expect", [
    ({"block_ids": [1], "probe_runs": {_SERIAL: [1]}},
     "selection.block_ids is retired: a derivative names its runs in selection.run_numbers"),
    ({"role": "canonical", "block_ids": [1, 2], "probe_runs": {_SERIAL: [1]}}, "selection.block_ids is retired"),
    ({"role": "derivative"}, "selection['role'] 'derivative' needs run_numbers: a derivative is its run set"),
])
def test_the_retired_block_selection_is_refused_naming_its_replacement(landed_session, prefix, selection, expect):
    """Design spec `2026-10-01-session-listing-and-run-requests-design.md`
    section 3.1: nothing but this repository's tests sent blocks, and a
    request that still does is a 422 that says what replaced them."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "runjob8", 8)
    idempotency_key = f"runjob8-{sorted(selection)!r}"
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, idempotency_key, **selection), prefix=prefix)
    assert not schema_request.Request & {"idempotency_key": idempotency_key}


# --- wl.works' bad-run marks and a replacement of a refused canonical (design spec
# `2026-11-24-wl-works-requests-design.md` sections 2 and 4.6). ---------------------------------


def _post_job(body: dict, *, prefix) -> tuple[int, dict]:
    """`POST /jobs` through the real handler, with `accept` behind it."""
    import functools
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from wl_preproc.responder.handler import make_handler
    from wl_preproc.responder.jobs import accept

    token = "test-bearer-token-1"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(token, lambda: None,
                                                              functools.partial(accept, prefix=prefix)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        http_request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/jobs", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(http_request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    finally:
        httpd.shutdown()
        httpd.server_close()


def _job_body(subject, when, key, *, runs=None, **selection) -> dict:
    return {"domain": "neural",
            "selection": {"session_datetime": when.isoformat(), "montage_id": 0, **selection},
            "parameters": {}, "idempotency_key": key,
            "metadata": {"runs": runs if runs is not None else _asserted(),
                         "probes": [], "experimenter": "jw", "subject": subject, "task_types": []}}


def test_a_marked_run_without_a_reason_is_a_422(landed_session, prefix):
    when = datetime.datetime(2027, 6, 1, 9, 0)
    landed_session("jbbad01", when, montages=[_M0])
    runs = _asserted()
    runs[1] = {**runs[1], "marked_bad": True}
    status, body = _post_job(_job_body("jbbad01", when, "jbbad01-k1", runs=runs), prefix=prefix)
    assert status == 422
    assert "run 2" in json.dumps(body)


def test_a_replacement_of_a_refused_canonical_is_accepted(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request as schema_request

    when = datetime.datetime(2027, 6, 1, 10, 0)
    session_key = landed_session("jbbad02", when, montages=[_M0])
    nwb_schema.activate(prefix=prefix)
    first = accept(_lifecycle_job("jbbad02", when, "jbbad02-k1"), prefix=prefix)
    nwb_schema.NwbFile.insert1({**first, "status": "refused", "built_at": datetime.datetime(2027, 6, 1, 11, 0),
                                "reason": "no blocks"})
    status, body = _post_job(_job_body("jbbad02", when, "jbbad02-k2", role="canonical",
                                       supersedes_activation_id=first["activation_id"]), prefix=prefix)
    assert status == 200, body
    assert body["accepted"] is True
    new_id = body["activation"]["activation_id"]
    row = (schema_request.Activation & {**session_key, "montage_id": 0, "activation_id": new_id}).fetch1()
    assert (row["role"], row["supersedes"]) == ("canonical", first["activation_id"])
    assert new_id == first["activation_id"] + 1


# --- What ends before it starts (design spec `2026-11-24-wl-works-requests-design.md`
# section 4.5): `GET /sessions` leaves such a run out, so wl.works can never assert it. ---------

# Run 2 ends before it starts; its start lies in montage 0's window [0, 12).
_REVERSED = [(1, 0.0, 4.0), (2, 11.0, 5.0), (3, 13.0, 16.0)]
_RUN_2_LEFT_OUT = ("run 2 ends at 5.000 s, before its start at 11.000 s: left out of this listing; "
                   "it cannot be requested")


def test_a_canonical_need_not_assert_a_reversed_run(landed_session, prefix):
    """A canonical asserting the window's other runs is accepted, and its
    file's runs leave run 2 out."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "jbrev01", 1, runs=_REVERSED)
    activation = accept(_runs_job(key, "jbrev01-k1", runs=[_REVERSED[0], _REVERSED[2]], probe_runs={_SERIAL: [1]}),
                        prefix=prefix)
    assert _run_rows(schema_request.ActivationRun, activation) == [(1,)]


@pytest.mark.parametrize("subject, runs, selection", [
    # Asserted in metadata.runs, with the times the listing would have given.
    ("jbrev02", _asserted(_REVERSED), {"probe_runs": {_SERIAL: [1]}}),
    # Named in a derivative's run_numbers, and not asserted.
    ("jbrev03", _asserted([_REVERSED[0], _REVERSED[2]]), {"run_numbers": [2]}),
])
def test_naming_a_reversed_run_is_a_422(landed_session, prefix, subject, runs, selection):
    """The 422 names the run and both its times, and nothing is written."""
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, subject, 2, runs=_REVERSED)
    body = _job_body(subject, key["session_datetime"], f"{subject}-k1", runs=runs, **selection)
    body["metadata"]["probes"] = [{"serial": _SERIAL, "insertion_number": 1}]
    status, answer = _post_job(body, prefix=prefix)
    assert status == 422, answer
    assert _RUN_2_LEFT_OUT in json.dumps(answer)
    assert (len(core.RunAssertion & key), len(schema_request.Activation & key)) == (0, 0)


def test_a_window_of_only_reversed_runs_is_a_422(landed_session, prefix):
    """Review focus 5: a canonical whose window holds only runs that end
    before they start is refused, naming them; it is not an empty file."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    measured = [(1, 4.0, 0.0), (2, 11.0, 5.0), (3, 13.0, 16.0)]
    key = _landed_with_runs(landed_session, "jbrev04", 4, runs=measured)
    expect = ("montage 0's window [0.0, 12.0) holds no run that can be requested: "
              "run 1 ends at 0.000 s, before its start at 4.000 s: left out of this listing; "
              "run 2 ends at 5.000 s, before its start at 11.000 s: left out of this listing")
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_runs_job(key, "jbrev04-k1", runs=[measured[2]], probe_runs={_SERIAL: []}), prefix=prefix)
    assert not schema_request.Activation & key


def _stored_before_the_fields_were_added(key: str) -> None:
    """Rewrite a stored request's payload as it was before `marked_bad`, `bad_reason` and `nickname` existed."""
    from wl_preproc.schema import request as schema_request

    stored = (schema_request.Request & {"idempotency_key": key}).fetch1("payload")
    for run in stored["metadata"]["runs"]:
        del run["marked_bad"], run["bad_reason"]
    del stored["metadata"]["subject_details"]["nickname"]
    schema_request.Request.update1({"idempotency_key": key, "payload": stored})


def test_a_retry_of_a_request_stored_before_a_field_was_added_is_the_same_request(landed_session, prefix):
    when = datetime.datetime(2027, 6, 2, 9, 0)
    landed_session("jbver01", when, montages=[_M0])
    body = _job_body("jbver01", when, "jbver01-k1")
    body["metadata"]["subject_details"] = {"species": "Macaca mulatta"}
    first_status, first = _post_job(body, prefix=prefix)
    assert first_status == 200, first
    _stored_before_the_fields_were_added("jbver01-k1")
    status, retried = _post_job(body, prefix=prefix)
    assert status == 200, retried
    assert retried["activation"] == first["activation"]


def test_a_retry_that_changes_a_field_is_still_key_reuse(landed_session, prefix):
    when = datetime.datetime(2027, 6, 2, 10, 0)
    landed_session("jbver02", when, montages=[_M0])
    body = _job_body("jbver02", when, "jbver02-k1")
    body["metadata"]["subject_details"] = {"species": "Macaca mulatta"}
    assert _post_job(body, prefix=prefix)[0] == 200
    _stored_before_the_fields_were_added("jbver02-k1")
    runs = _asserted()
    runs[0] = {**runs[0], "marked_bad": True, "bad_reason": "x"}
    changed = _job_body("jbver02", when, "jbver02-k1", runs=runs)
    changed["metadata"]["subject_details"] = body["metadata"]["subject_details"]
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema.request import KeyReuseError

    # `accept` raises the module's own KeyReuseError; `server.py`'s seam is what turns it into the 409.
    with pytest.raises(KeyReuseError, match="payload differs"):
        accept(JobRequest.model_validate(changed), prefix=prefix)


# --- A request names a montage this host listed (design spec `2026-11-24-montages-design.md` sections 4.4, 4.6
# and 5; plan decision D14 and Review Focus 3). -------------------------------------------------------------------

_M1 = {"montage_id": 1, "start_s": 0.0, "end_s": 12.0}


def _session_of(key: dict) -> str:
    return f"{key['subject']}/{key['session_datetime'].isoformat()}"


def _nothing_written(key: dict, idempotency_key: str) -> bool:
    from wl_preproc.schema import core
    from wl_preproc.schema import request as schema_request

    return not (core.RunAssertion & key or schema_request.Activation & key or schema_request.SharedActivation & key
                or schema_request.Request & {"idempotency_key": idempotency_key})


def test_a_canonical_names_a_listed_montage(landed_session, prefix):
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "jblist01", 21, montages=())
    seed_montage(key, {"montage_id": 1, "start_s": 0.0, "end_s": 12.0})
    activation = accept(_runs_job(key, "jblist01-k1", serials=(), montage_id=1), prefix=prefix)
    assert (activation["montage_id"], (schema_request.Activation & activation).fetch1("role")) == (1, "canonical")
    assert _run_rows(schema_request.ActivationRun, activation) == [(1,), (2,)]


def test_a_montage_not_listed_is_a_422_naming_the_status(landed_session, prefix):
    """Review Focus 3: a request built from an older listing names a montage that is no longer there, and is told
    why, never given a file over the wrong runs. With no derivation stored the session is `waiting` (D13)."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    ambiguous = _landed_with_runs(landed_session, "jblist02", 22, montages=())
    core.MontageDerivation.insert1({**ambiguous, "positions_status": "ambiguous", "positions_seq": 2,
                                    "derived_at": datetime.datetime(2027, 9, 22, 10, 0)})
    waiting = _landed_with_runs(landed_session, "jblist03", 23, montages=())
    # Listed at seq 1 as montages 1 and 2; seq 2 left one.
    placed = _landed_with_runs(landed_session, "jblist04", 24, montages=(_M1,))
    for key, montage_id, status in ((ambiguous, 1, "ambiguous"), (waiting, 1, "waiting"), (placed, 2, "placed")):
        idempotency_key = f"{key['subject']}-k1"
        with pytest.raises(ValueError) as refused:
            accept(_runs_job(key, idempotency_key, serials=(), montage_id=montage_id), prefix=prefix)
        assert str(refused.value) == (
            f"no montage {montage_id} is listed for {_session_of(key)}: positions_status is {status}; send PUT "
            "/positions, or rebuild the request from a fresh GET /sessions")
        assert _nothing_written(key, idempotency_key)


def test_probe_runs_must_be_sortable(landed_session, prefix):
    """Section 4.4: a probe's runs are among the ones its montage lists as sortable for it."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "jbsort01", 25, montages=())
    seed_montage(key, _M1, probes=[{"serial": "S1", "insertion_number": 1, "sortable_runs": [1]}])
    with pytest.raises(ValueError) as refused:
        accept(_runs_job(key, "jbsort01-k1", serials=("S1",), montage_id=1, probe_runs={"S1": [1, 2]}),
               prefix=prefix)
    assert str(refused.value) == "probe S1's run(s) [2] are not among its sortable runs [1] in montage 1"
    assert _nothing_written(key, "jbsort01-k1")
    activation = accept(_runs_job(key, "jbsort01-k2", serials=("S1",), montage_id=1, probe_runs={"S1": [1]}),
                        prefix=prefix)
    assert _run_rows(schema_request.ActivationProbeRun, activation) == [("S1", 1)]


def _positions(key: dict, *probes: tuple[str, int], prefix, seq: int = 1) -> None:
    """`PUT /positions` at `seq` for `probes` ((serial, insertion_number)), each inserted before the session."""
    put(key, [{"serial": serial, "insertion_number": number,
               "inserted_at": key["session_datetime"] - datetime.timedelta(minutes=10)} for serial, number in probes],
        seq=seq, prefix=prefix)


def test_a_probe_must_name_its_position(landed_session, prefix):
    """D14: with positions stored, each of `metadata.probes` is one of them, by serial and insertion number."""
    from wl_preproc.responder.jobs import accept

    key = _landed_with_runs(landed_session, "jbpos01", 26, montages=())
    _positions(key, ("S1", 1), prefix=prefix)
    seed_montage(key, _M1, probes=[{"serial": "S1", "insertion_number": 1, "sortable_runs": [1, 2]}])
    for serial, number in (("S2", 1), ("S1", 2)):
        idempotency_key = f"jbpos01-{serial}-{number}"
        with pytest.raises(ValueError) as refused:
            accept(_runs_job(key, idempotency_key, montage_id=1,
                             probes=[{"serial": serial, "insertion_number": number}], probe_runs={serial: []}),
                   prefix=prefix)
        assert f"probe {serial} at insertion_number {number}" in str(refused.value)
        assert _nothing_written(key, idempotency_key)
    accept(_runs_job(key, "jbpos01-k1", montage_id=1, probes=[{"serial": "S1", "insertion_number": 1}],
                     probe_runs={"S1": [1, 2]}), prefix=prefix)


def test_a_probe_with_no_position_in_the_montage_sorts_nothing(landed_session, prefix):
    """D14: S2's position covers no run of montage 1, so the montage does not list it, and it sorts no run there."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "jbpos02", 27, montages=())
    _positions(key, ("S1", 1), ("S2", 2), prefix=prefix)
    seed_montage(key, _M1, probes=[{"serial": "S1", "insertion_number": 1, "sortable_runs": [1, 2]}])
    probes = [{"serial": "S1", "insertion_number": 1}, {"serial": "S2", "insertion_number": 2}]
    with pytest.raises(ValueError) as refused:
        accept(_runs_job(key, "jbpos02-k1", montage_id=1, probes=probes, probe_runs={"S1": [1], "S2": [1]}),
               prefix=prefix)
    assert str(refused.value) == ("probe S2 holds no position in montage 1, so it sorts no run there; the request "
                                  "gives it run(s) [1]")
    assert _nothing_written(key, "jbpos02-k1")
    activation = accept(_runs_job(key, "jbpos02-k2", montage_id=1, probes=probes, probe_runs={"S1": [1], "S2": []}),
                        prefix=prefix)
    assert _run_rows(schema_request.ActivationProbeRun, activation) == [("S1", 1)]


def test_a_derivative_over_a_run_not_sortable_for_its_probe_is_a_422(landed_session, prefix):
    """D14: a derivative's runs are every named probe's, so a run one of them cannot be sorted on is refused,
    never quietly narrowed for that probe."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request

    key = _landed_with_runs(landed_session, "jbsort02", 28, montages=())
    seed_montage(key, _M1, probes=[{"serial": "S1", "insertion_number": 1, "sortable_runs": [1]}])
    with pytest.raises(ValueError) as refused:
        accept(_runs_job(key, "jbsort02-k1", serials=("S1",), montage_id=1, run_numbers=[1, 2]), prefix=prefix)
    assert str(refused.value) == "probe S1's run(s) [2] are not among its sortable runs [1] in montage 1"
    assert _nothing_written(key, "jbsort02-k1")
    activation = accept(_runs_job(key, "jbsort02-k2", serials=("S1",), montage_id=1, run_numbers=[1]),
                        prefix=prefix)
    assert _run_rows(schema_request.ActivationProbeRun, activation) == [("S1", 1)]


def test_a_derivation_rewritten_while_a_request_is_checked_is_a_422(landed_session, prefix, monkeypatch):
    """The race the stage could win: it re-derives the session after `accept()` has checked the montage and before
    the activation is written. The insert's transaction takes the derivation's lock and finds it changed, so the
    request is refused whole rather than landing on a montage it was never checked against."""
    from wl_preproc.montage import stored
    from wl_preproc.responder import jobs

    key = _landed_with_runs(landed_session, "jbrace01", 29, montages=())
    seed_montage(key, _M1)
    checked = jobs._check_probes

    def checked_then_rederived(session_key, probes):
        checked(session_key, probes)
        stored.write(key, stored.StoredMontages(
            status="placed", positions_seq=None, flags=(),
            montages=({"montage_id": 1, "start_s": 0.0, "end_s": 6.0, "probes": ()},), groups=()))

    monkeypatch.setattr(jobs, "_check_probes", checked_then_rederived)
    with pytest.raises(ValueError) as refused:
        jobs.accept(_runs_job(key, "jbrace01-k1", serials=(), montage_id=1), prefix=prefix)
    assert str(refused.value) == (
        f"the montages of {_session_of(key)} were derived again while this request was checked: positions_status "
        "is placed; rebuild the request from a fresh GET /sessions")
    assert _nothing_written(key, "jbrace01-k1")


@pytest.mark.parametrize("status", ["waiting", "ambiguous"])
def test_a_montage_under_a_derivation_that_lists_none_is_a_422(landed_session, prefix, status):
    """D13: montages are listed only at `placed` and `not_needed`, so one stored under any other status is not a
    listed montage, and a request naming it is refused with the status named."""
    from wl_preproc.responder.jobs import accept

    key = _landed_with_runs(landed_session, {"waiting": "jbstw01", "ambiguous": "jbsta01"}[status], 3, montages=())
    seed_montage(key, _M1, status=status)
    idempotency_key = f"{key['subject']}-k1"
    with pytest.raises(ValueError) as refused:
        accept(_runs_job(key, idempotency_key, serials=(), montage_id=1), prefix=prefix)
    assert str(refused.value) == (
        f"no montage 1 is listed for {_session_of(key)}: positions_status is {status}; send PUT /positions, or "
        "rebuild the request from a fresh GET /sessions")
    assert _nothing_written(key, idempotency_key)


def _positions_changed(key: dict) -> str:
    return (f"session {key['subject']} {key['session_datetime'].isoformat()}: its positions changed (seq 2) since "
            "its montages were derived (seq 1); send the request again once GET /sessions lists seq 2")


def test_positions_sent_since_the_montages_were_derived_are_a_422(landed_session, prefix):
    """D17: a `PUT /positions` stores only, and the next pass derives and lists. A request arriving between them
    names montages of the earlier seq; accepted, it would fix them under the new positions (D8). It is told so,
    not that its probe names no position: seq 2 renumbered S1's insertion."""
    from wl_preproc.responder.jobs import accept

    key = _landed_with_runs(landed_session, "jbseq01", 30, montages=())
    _positions(key, ("S1", 1), prefix=prefix)
    seed_montage(key, _M1, probes=[{"serial": "S1", "insertion_number": 1, "sortable_runs": [1, 2]}])
    _positions(key, ("S1", 2), prefix=prefix, seq=2)
    with pytest.raises(ValueError) as refused:
        accept(_runs_job(key, "jbseq01-k1", montage_id=1, probes=[{"serial": "S1", "insertion_number": 1}],
                         probe_runs={"S1": [1, 2]}), prefix=prefix)
    assert str(refused.value) == _positions_changed(key)
    assert _nothing_written(key, "jbseq01-k1")


def test_positions_sent_while_a_request_is_checked_are_a_422(landed_session, prefix, monkeypatch):
    """The same refusal when the `PUT` lands after the checks and before the activation is written: the insert's
    transaction reads the stored seq under a lock, never from the snapshot it began with."""
    from wl_preproc.responder import jobs

    key = _landed_with_runs(landed_session, "jbseq02", 30, montages=())
    _positions(key, ("S1", 1), prefix=prefix)
    seed_montage(key, _M1, probes=[{"serial": "S1", "insertion_number": 1, "sortable_runs": [1, 2]}])
    checked = jobs._check_probes

    def checked_then_moved(session_key, probes):
        checked(session_key, probes)
        _positions(key, ("S1", 1), ("S2", 2), prefix=prefix, seq=2)

    monkeypatch.setattr(jobs, "_check_probes", checked_then_moved)
    with pytest.raises(ValueError) as refused:
        jobs.accept(_runs_job(key, "jbseq02-k1", montage_id=1, probes=[{"serial": "S1", "insertion_number": 1}],
                              probe_runs={"S1": [1, 2]}), prefix=prefix)
    assert str(refused.value) == _positions_changed(key)
    assert _nothing_written(key, "jbseq02-k1")


def test_positions_sent_to_a_session_derived_with_none_are_a_422_saying_so(landed_session, prefix):
    """A session derived before any positions were stored has no seq to name: the 422 says so."""
    from wl_preproc.responder.jobs import accept

    key = _landed_with_runs(landed_session, "jbseq03", 30, montages=())
    seed_montage(key, _M1, status="not_needed")
    _positions(key, ("S1", 1), prefix=prefix)
    with pytest.raises(ValueError) as refused:
        accept(_runs_job(key, "jbseq03-k1", serials=(), montage_id=1), prefix=prefix)
    assert str(refused.value) == (
        f"session {key['subject']} {key['session_datetime'].isoformat()}: its positions changed (seq 1) since its "
        "montages were derived with no positions; send the request again once GET /sessions lists seq 1")
    assert _nothing_written(key, "jbseq03-k1")


# --- A shared group's request (design spec `2026-11-24-montages-design.md` section 4.6; plan decisions D11,
# D12). Group 1 is S1 at insertion 1 over montages 1 [0, 5) and 2 [5, 24), sortable on runs 1 and 2; S1 sorts no
# run 3 in montage 2. ---------------------------------------------------------------------------------------------


def _shared_session(landed_session, subject: str, *, prefix) -> dict:
    key = landed_session(subject, datetime.datetime(2027, 10, 1, 9, 0))
    _positions(key, ("S1", 1), ("S2", 2), prefix=prefix)
    seed_montage(key, {"montage_id": 1, "start_s": 0.0, "end_s": 5.0},
                 probes=[{"serial": "S1", "insertion_number": 1, "sortable_runs": [1]}])
    seed_montage(key, {"montage_id": 2, "start_s": 5.0, "end_s": 24.0},
                 probes=[{"serial": "S1", "insertion_number": 1, "sortable_runs": [2]},
                         {"serial": "S2", "insertion_number": 2, "sortable_runs": [2, 3]}])
    seed_group(key, {"group_id": 1, "serial": "S1", "insertion_number": 1, "montage_ids": [1, 2],
                     "sortable_runs": [1, 2]})
    return key


def _shared_job(key: dict, idempotency_key: str, *, runs=_RUNS, probes=None, **selection) -> JobRequest:
    """Group 1's request, asserting `runs`, with S1 at insertion 1 sorted on runs 1 and 2 unless `selection` or
    `probes` say otherwise."""
    return JobRequest(
        domain="neural",
        selection={"role": "shared", "group_id": 1, "session_datetime": key["session_datetime"],
                   "probe_runs": {"S1": [1, 2]}, **selection},
        parameters={},
        idempotency_key=idempotency_key,
        metadata=MetadataBundle(runs=_asserted(runs), experimenter="jw", subject=key["subject"], task_types=[],
                                probes=probes if probes is not None else [{"serial": "S1", "insertion_number": 1}]),
    )


def test_a_shared_request_is_stored_and_waits(landed_session, prefix):
    """Section 4.6: accepted and stored, its build waiting on sorting. Its one probe's report is written as a
    canonical's is, and the NWB stage has nothing to build for it."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core, ephys
    from wl_preproc.schema import request as schema_request

    key = _shared_session(landed_session, "jbshr01", prefix=prefix)
    activation = accept(_shared_job(key, "jbshr01-k1"), prefix=prefix)
    assert activation == {**key, "group_id": 1, "activation_id": 0}
    assert (schema_request.SharedActivation & activation).fetch1("request_key") == "jbshr01-k1"
    assert sorted(int(n) for n in (schema_request.SharedActivation.ProbeRun & activation).to_arrays("run_number")) \
        == [1, 2]
    assert not schema_request.Activation & key
    assert [(int(r["insertion_number"]), r["probe_serial"]) for r in (ephys.InsertionReport & key).to_dicts()] == [
        (1, "S1")]
    assert sorted(int(n) for n in (core.RunAssertion & key).to_arrays("run_number")) == [1, 2, 3]


def test_a_shared_request_answers_as_a_canonical(landed_session, prefix):
    """Q4: the same key again is a retry, another key for the group returns its activation, and the first key
    with another payload is key reuse. Over HTTP the answer is a canonical's 200."""
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import request as schema_request
    from wl_preproc.schema.request import KeyReuseError

    key = _shared_session(landed_session, "jbshr02", prefix=prefix)
    first = accept(_shared_job(key, "jbshr02-k1"), prefix=prefix)
    assert accept(_shared_job(key, "jbshr02-k1"), prefix=prefix) == first
    assert accept(_shared_job(key, "jbshr02-k2"), prefix=prefix) == first
    assert len(schema_request.SharedActivation & key) == 1
    assert schema_request.Request & {"idempotency_key": "jbshr02-k2"}
    with pytest.raises(KeyReuseError, match="payload differs"):
        accept(_shared_job(key, "jbshr02-k1", probe_runs={"S1": [1]}), prefix=prefix)
    body = _shared_job(key, "jbshr02-k1").model_dump(mode="json")
    assert _post_job(body, prefix=prefix) == (200, {"activation": {
        "subject": "jbshr02", "session_datetime": key["session_datetime"].isoformat(), "group_id": 1,
        "activation_id": 0}, "accepted": True})


_TWO_PROBES = [{"serial": "S1", "insertion_number": 1}, {"serial": "S2", "insertion_number": 2}]


@pytest.mark.parametrize("subject, change, expect", [
    ("jbshg01", {"montage_id": 1},
     "selection.montage_id is a canonical's or a derivative's: a shared request names its group in "
     "selection.group_id"),
    ("jbshg02", {"run_numbers": [1, 2]},
     "selection.run_numbers is a derivative's: a shared request names its probe's runs in selection.probe_runs"),
    ("jbshg03", {"supersedes_activation_id": 0},
     "selection['supersedes_activation_id'] needs selection['role'] == 'canonical'"),
    ("jbshg04", {"group_id": 9},
     "no shared group 9 is listed for jbshg04/2027-10-01T09:00:00: positions_status is placed"),
    ("jbshg05", {"probes": _TWO_PROBES},
     "metadata.probes names 2 probes, and a shared request names its group's one: group 1 is probe S1 at "
     "insertion_number 1"),
    ("jbshg06", {"probes": _TWO_PROBES[1:], "probe_runs": {"S2": [2]}},
     "metadata.probes names probe S2 at insertion_number 2, and group 1 is probe S1 at insertion_number 1"),
    ("jbshg07", {"probe_runs": {"S2": [1, 2]}},
     "selection.probe_runs names ['S2'], and group 1 is probe S1: a shared request states its one probe's runs"),
    ("jbshg08", {"probe_runs": {"S1": [1, 2, 3]}},
     "selection.probe_runs['S1'] names run(s) [3] not among group 1's sortable runs [1, 2]"),
    ("jbshg09", {"runs": _RUNS[:1]},
     "selection.probe_runs['S1'] names run(s) [2] asserted neither in metadata.runs nor before"),
])
def test_a_shared_request_names_only_its_group(landed_session, prefix, subject, change, expect):
    """D11, D12: a shared request names its listed group, that group's one probe, and runs among the group's
    sortable runs, each asserted now or before. Anything else is a 422 naming the field, and writes nothing."""
    from wl_preproc.responder.jobs import accept

    key = _shared_session(landed_session, subject, prefix=prefix)
    change = dict(change)
    fields = {name: change.pop(name) for name in ("runs", "probes") if name in change}
    with pytest.raises(ValueError, match=re.escape(expect)):
        accept(_shared_job(key, f"{subject}-k1", **fields, **change), prefix=prefix)
    assert _nothing_written(key, f"{subject}-k1")


def test_a_shared_request_on_a_group_derived_again_while_checked_is_a_422(landed_session, prefix, monkeypatch):
    """The race `_hold_derivation` closes for a canonical, for a shared request: the stage re-derives the session
    after the group was checked and before the activation is written, and the request is refused whole."""
    from wl_preproc.montage import stored
    from wl_preproc.responder import jobs

    key = _shared_session(landed_session, "jbshrace", prefix=prefix)
    checked = jobs._check_probes

    def checked_then_rederived(session_key, probes):
        checked(session_key, probes)
        stored.write(key, stored.StoredMontages(
            status="placed", positions_seq=1, flags=(),
            montages=({"montage_id": 1, "start_s": 0.0, "end_s": 24.0, "probes": ()},), groups=()))

    monkeypatch.setattr(jobs, "_check_probes", checked_then_rederived)
    with pytest.raises(ValueError) as refused:
        jobs.accept(_shared_job(key, "jbshrace-k1"), prefix=prefix)
    assert str(refused.value) == (
        f"the montages of {_session_of(key)} were derived again while this request was checked: positions_status "
        "is placed; rebuild the request from a fresh GET /sessions")
    assert _nothing_written(key, "jbshrace-k1")


@pytest.mark.parametrize("subject, probe_runs", [("jbshe01", {"S1": []}), ("jbshe02", None)])
def test_a_shared_request_names_at_least_one_run(landed_session, prefix, subject, probe_runs):
    """D11: a group has one shared activation, never superseded, so one with no run would hold the group's sort for
    good with nothing to sort. A missing or empty list for its probe is a 422, and writes nothing."""
    from wl_preproc.responder.jobs import accept

    key = _shared_session(landed_session, subject, prefix=prefix)
    job = _shared_job(key, f"{subject}-k1", probe_runs=probe_runs)
    if probe_runs is None:
        del job.selection["probe_runs"]
    with pytest.raises(ValueError) as refused:
        accept(job, prefix=prefix)
    assert str(refused.value) == ("group 1: a shared request names at least one of its sortable runs for probe S1 in "
                                  "selection.probe_runs")
    assert _nothing_written(key, f"{subject}-k1")

import datetime

import pytest
from pydantic import ValidationError

from wl_preproc.contracts.protocol import (
    Action,
    HealthResponse,
    JobRequest,
    MetadataBundle,
    PositionsRequest,
    ProbePosition,
    Reading,
    as_naive_utc,
    contains_markup,
    plain_text,
)

# Verbatim from wl.works Plan 10 section 4.
PLAN_10_EXAMPLE = {
    "verdict": "ok",
    "readings": [
        {"key": "transfer", "label": "Latest transfer", "value": "complete", "featured": False},
        {
            "key": "spike-sort",
            "label": "Spike sorting",
            "value": "4 of 7 sessions",
            "featured": True,
        },
    ],
    "actions": [{"name": "start-preproc", "label": "Start preprocessing run"}],
}


def test_plan_10_example_validates():
    response = HealthResponse.model_validate(PLAN_10_EXAMPLE)
    assert response.verdict == "ok"
    assert response.readings[1].featured is True
    assert response.actions[0].name == "start-preproc"


@pytest.mark.parametrize("text", ["<b>bold</b>", "plain <img src=x>", "a & b", "line<br>break"])
def test_contains_markup_detects(text):
    assert contains_markup(text) is True


@pytest.mark.parametrize("text", ["complete", "4 of 7 sessions", "rig-a: 12 units"])
def test_contains_markup_allows_plain(text):
    assert contains_markup(text) is False


def test_plain_text_removes_every_markup_character():
    result = plain_text("A&B<C>D")
    assert not contains_markup(result), f"still contains markup: {result!r}"


def test_plain_text_never_collapses_two_different_characters_onto_one_placeholder():
    """Every producer of untrusted text (currently `responder/health.py`)
    depends on this: substituting `<`, `>` and `&` onto ONE shared
    placeholder would make `A&B` and `A<B` -- two different facts about a
    real fault -- render identically, which is the exact rule `Reading`'s
    own markup ban already argues from for a different reason.
    """
    substituted = {plain_text("A&B"), plain_text("A<B"), plain_text("A>B")}
    assert len(substituted) == 3, f"two distinct inputs collapsed onto one output: {substituted}"


def test_plain_text_is_a_no_op_on_text_with_no_markup():
    assert plain_text("4 of 7 sessions") == "4 of 7 sessions"


def test_plain_text_output_is_itself_accepted_by_reading():
    """The whole point: the substituted text must be constructible, not just
    markup-free in the abstract."""
    value = plain_text("path 'A&B<C>' denied")
    Reading(key="k", label="L", value=value, featured=False)  # must not raise


def test_reading_rejects_markup_in_label():
    """wl.works treats this host as untrusted and renders labels as escaped text."""
    with pytest.raises(ValidationError):
        Reading(key="k", label="<b>Spike sorting</b>", value="ok", featured=False)


def test_reading_rejects_markup_in_value():
    with pytest.raises(ValidationError):
        Reading(key="k", label="Spike sorting", value="4 <b>of</b> 7", featured=False)


def test_action_rejects_markup_in_label():
    with pytest.raises(ValidationError):
        Action(name="start-preproc", label="Start <i>preprocessing</i>")


def test_health_response_rejects_unknown_key():
    with pytest.raises(ValidationError):
        HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdcit": "ok"})


def test_an_unknown_verdict_is_rejected():
    """wl.works validates verdict against exactly four values and refuses the
    whole response otherwise, so a bare `str` here means a typo ships and
    fails at their end rather than ours. Plan 10 section 1.1."""
    with pytest.raises(ValidationError):
        HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdict": "okay"})


@pytest.mark.parametrize("verdict", ["ok", "degraded", "down", "unknown"])
def test_every_verdict_wl_works_accepts_validates_here(verdict):
    assert HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdict": verdict}).verdict == verdict


def test_job_request_carries_the_metadata_bundle():
    """wl-preproc cannot fetch from wl.works, so metadata must arrive inbound."""
    request = JobRequest(
        domain="neural",
        selection={"session_id": "2027-03-14_01", "montage_id": 1},
        parameters={"clustering_paramset": "ks4_default"},
        idempotency_key="a1b2c3",
        metadata=MetadataBundle(
            runs=[{"run_number": 1, "start_s": 0.0, "end_s": 1800.0, "works_run_id": "wr-1"}],
            montage_boundaries=[{"montage_id": 1, "start_s": 0.0, "end_s": 3600.0}],
            probes=[{"serial": "NP-1234", "insertion_number": 1}],
            experimenter="jw",
            subject="pico",
            task_types=["rf_map", "resting_dark"],
        ),
    )
    assert request.metadata.subject == "pico"
    assert request.metadata.montage_boundaries[0].montage_id == 1


def test_job_request_rejects_unknown_key():
    with pytest.raises(ValidationError):
        JobRequest.model_validate(
            {
                "domain": "neural",
                "selection": {},
                "parameters": {},
                "idempotency_key": "x",
                "metadata": {
                    "blocks": [],
                    "montage_boundaries": [],
                    "probes": [],
                    "experimenter": "jw",
                    "subject": "pico",
                    "task_types": [],
                },
                "priorty": 3,
            }
        )


# --- The two payload holes, closed 2026-08-26. -------------------------------
#
# Both were named in `handoffs/2026-08-23-next-session-phase-2b-is-hardware-
# blocked.md` and both are the same fault in two places: a `list[dict[str,
# Any]]` records nothing a second implementer can build against. Design spec
# section 11.2 makes this payload a frozen interface precisely because wl.works'
# 18b tests are contract tests against a FAKE wl-preproc, and a fake can only be
# built from what the contract writes down. An untyped list exports to
# `{"type": "object", "additionalProperties": true}` -- which is to say, it
# exports nothing.


def _bundle(**overrides):
    """A minimal valid bundle, so each test below varies exactly one thing."""
    return {
        "blocks": [],
        "montage_boundaries": [{"montage_id": 1, "start_s": 0.0, "end_s": 3600.0}],
        "probes": [{"serial": "NP-1234", "insertion_number": 1}],
        "experimenter": "jw",
        "subject": "pico",
        "task_types": [],
        **overrides,
    }


def test_a_montage_boundary_is_a_model_not_a_bare_dict():
    bundle = MetadataBundle.model_validate(_bundle())
    assert bundle.montage_boundaries[0].montage_id == 1
    assert bundle.montage_boundaries[0].start_s == 0.0
    assert bundle.montage_boundaries[0].end_s == 3600.0


def test_a_montage_id_beyond_tinyint_is_refused_at_the_contract():
    """`core.Montage.montage_id` is a tinyint. 128 does not fit, and the
    refusal belongs in the exported schema where wl.works can see it -- not
    only in `responder/jobs.py`, which nobody outside this repository reads."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(montage_boundaries=[{"montage_id": 128, "start_s": 0.0, "end_s": 1.0}])
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_montage_boundary_is_refused(bad):
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(montage_boundaries=[{"montage_id": 0, "start_s": bad, "end_s": 1.0}])
        )


def test_a_montage_boundary_missing_a_field_is_refused():
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(montage_boundaries=[{"montage_id": 0, "start_s": 0.0}])
        )


def test_an_unknown_key_on_a_montage_boundary_is_refused():
    """`extra="forbid"` everywhere else in this module. A frozen interface that
    silently swallows a key it does not know is how two implementations drift
    apart while both look correct."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(
                montage_boundaries=[
                    {"montage_id": 0, "start_s": 0.0, "end_s": 1.0, "bank": "A"}
                ]
            )
        )


def test_a_probe_entry_carries_the_trajectory_it_ran_against():
    """Design spec section 11.2's payload block lists `trajectory_id per
    insertion`; wl-works' trajectory-identity design section 8.1 says the same
    from the other side. `ephys.ProbeInsertion.trajectory_id` has been waiting
    for it since Phase 2a."""
    bundle = MetadataBundle.model_validate(
        _bundle(probes=[{"serial": "NP-1234", "insertion_number": 1, "trajectory_id": "T-0042"}])
    )
    assert bundle.probes[0].trajectory_id == "T-0042"


def test_a_penetration_with_no_trajectory_omits_it_rather_than_inventing_one():
    """Ruled 2026-08-26: probes are sometimes inserted along a trajectory that
    was never planned, so there is nothing to name. Absence is a legitimate
    permanent state, not only a not-yet."""
    bundle = MetadataBundle.model_validate(_bundle())
    assert bundle.probes[0].trajectory_id is None


def test_a_trajectory_id_longer_than_the_column_is_refused():
    """`ephys.ProbeInsertion.trajectory_id` is varchar(64). A 65th character is
    silently truncated by MySQL in non-strict mode, which would store a
    reference to a DIFFERENT trajectory than the one the ELN named."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "NP-1", "insertion_number": 1, "trajectory_id": "T" * 65}])
        )


def test_an_unknown_key_on_a_probe_entry_is_refused():
    """The case this exists for: `trajectroy_id`. Under a bare dict it rides
    along unread, and every insertion silently records no trajectory."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "NP-1", "insertion_number": 1, "trajectroy_id": "T-1"}])
        )


def test_a_probe_entry_requires_its_serial_and_insertion_number():
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(_bundle(probes=[{"serial": "NP-1"}]))


def test_a_probe_serial_longer_than_the_column_is_refused():
    """`ephys.Probe.probe_serial` is varchar(32)."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "N" * 33, "insertion_number": 1}])
        )


def test_an_insertion_number_beyond_tinyint_unsigned_is_refused():
    """`ephys.ProbeInsertion.insertion_number` is tinyint unsigned."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "NP-1", "insertion_number": 256}])
        )


# -- The aim and the latest assignment (design spec
# `2026-09-30-nwb-probes-design.md` section 4).

_TARGET = {"area": "V4d", "atlas": "CHARM", "atlas_level": 6}
_ASSIGNMENT = {"area": "V4d", "source": "at_rig", "asserted_at": "2025-06-11T09:30:00Z"}


def test_a_probe_entry_carries_its_aim_and_its_latest_assignment():
    bundle = MetadataBundle.model_validate(_bundle(probes=[
        {"serial": "NP-1", "insertion_number": 1, "target": _TARGET, "area_assignment": _ASSIGNMENT}]))
    probe = bundle.probes[0]
    assert (probe.target.area, probe.target.atlas, probe.target.atlas_level) == ("V4d", "CHARM", 6)
    assert (probe.area_assignment.area, probe.area_assignment.source) == ("V4d", "at_rig")


def test_a_request_without_either_stays_valid():
    probe = MetadataBundle.model_validate(_bundle()).probes[0]
    assert probe.target is None and probe.area_assignment is None


@pytest.mark.parametrize("target", [
    {"area": "V4d", "atlas": "CHARM"},  # all three or none
    {**_TARGET, "area": "A" * 33},  # InsertionLocation.area : varchar(32)
    {**_TARGET, "atlas_level": 256},  # atlas_level : tinyint unsigned
    {**_TARGET, "area": ""},
])
def test_a_partial_or_oversized_target_is_refused(target):
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(_bundle(probes=[{"serial": "NP-1", "insertion_number": 1, "target": target}]))


@pytest.mark.parametrize("assignment", [
    {**_ASSIGNMENT, "source": "guess"},  # wl.works' six sources only
    {key: value for key, value in _ASSIGNMENT.items() if key != "asserted_at"},
    {**_ASSIGNMENT, "atlas": "CHARM"},  # Plan 19's assignment row names no atlas
])
def test_an_assignment_outside_wl_works_own_shape_is_refused(assignment):
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "NP-1", "insertion_number": 1, "area_assignment": assignment}]))


@pytest.mark.parametrize("entry", [
    {"run_number": 0, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w"},
    {"run_number": 32768, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w"},
    {"run_number": 1, "start_s": float("nan"), "end_s": 1.0, "works_run_id": "w"},
    {"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": ""},
    {"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w" * 65},
    {"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w", "block_id": 1},
])
def test_a_run_entry_holds_a_measured_runs_number_times_and_wl_works_id(entry):
    """`metadata.runs`, typed as `metadata.blocks` never was (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3.1): a
    run number core.Run can hold, finite times, and an id its column holds."""
    from pydantic import ValidationError

    from wl_preproc.contracts.protocol import RunEntry

    RunEntry.model_validate({"run_number": 1, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w"})
    with pytest.raises(ValidationError):
        RunEntry.model_validate(entry)


def test_metadata_blocks_are_refused_naming_metadata_runs():
    """Retired (design spec `2026-10-01-session-listing-and-run-requests-design.md`
    section 3.1): a request asserts runs, and one still sending blocks is told
    what replaced them, not that the field is unknown. An empty list, which
    every request sent while the field was required, says nothing."""
    import re

    with pytest.raises(ValidationError, match=re.escape("metadata.blocks is retired: a request asserts its runs "
                                                        "in metadata.runs")):
        MetadataBundle.model_validate(_bundle(blocks=[{"block_id": 1, "task_type": "rf_map"}]))
    assert MetadataBundle.model_validate(_bundle()).blocks == []
    assert MetadataBundle.model_validate({k: v for k, v in _bundle().items() if k != "blocks"}).runs == []
    schema = MetadataBundle.model_json_schema()
    blocks = schema["properties"]["blocks"]
    assert "blocks" not in schema["required"] and (blocks["maxItems"], blocks["deprecated"]) == (0, True)


# --- wl.works' bad-run marks, the animal's nickname, a bounded experimenter. -----------------------
# Design spec `2026-11-24-wl-works-requests-design.md` sections 2, 4.1, 4.2 and 4.6.


def _run_entry(**fields):
    return {"run_number": 7, "start_s": 0.0, "end_s": 1.0, "works_run_id": "w", **fields}


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_a_marked_run_needs_a_reason(reason):
    from wl_preproc.contracts.protocol import RunEntry

    with pytest.raises(ValidationError, match="run_number|run 7") as excinfo:
        RunEntry.model_validate(_run_entry(marked_bad=True, bad_reason=reason))
    assert "run 7" in str(excinfo.value)


def test_a_reason_needs_a_mark():
    from wl_preproc.contracts.protocol import RunEntry

    with pytest.raises(ValidationError, match="run 7"):
        RunEntry.model_validate(_run_entry(bad_reason="x"))
    with pytest.raises(ValidationError, match="run 7"):
        RunEntry.model_validate(_run_entry(marked_bad=False, bad_reason="x"))


def test_a_marked_run_with_a_reason_is_accepted():
    from wl_preproc.contracts.protocol import RunEntry

    entry = RunEntry.model_validate(_run_entry(marked_bad=True, bad_reason="eye lost"))
    assert (entry.marked_bad, entry.bad_reason) == (True, "eye lost")
    assert RunEntry.model_validate(entry.model_dump()) == entry
    assert RunEntry.model_validate(_run_entry(marked_bad=True, bad_reason="x" * 1000)).bad_reason == "x" * 1000
    with pytest.raises(ValidationError):
        RunEntry.model_validate(_run_entry(marked_bad=True, bad_reason="x" * 1001))


def test_nickname_is_one_to_sixty_four_characters():
    from wl_preproc.contracts.protocol import SubjectDetails

    for bad in ("", "n" * 65):
        with pytest.raises(ValidationError):
            SubjectDetails.model_validate({"nickname": bad})
    for good in ("Pico", "n" * 64):
        assert SubjectDetails.model_validate({"nickname": good}).nickname == good


def test_a_nickname_of_only_white_space_is_refused():
    from wl_preproc.contracts.protocol import SubjectDetails

    for blank in (" ", "    ", "\t\n"):
        with pytest.raises(ValidationError, match="nickname"):
            SubjectDetails.model_validate({"nickname": blank})
    assert SubjectDetails.model_validate({"nickname": " Pico "}).nickname == " Pico "


def test_experimenter_is_at_most_sixty_four_characters():
    for good in ("e" * 64, ""):
        assert MetadataBundle.model_validate(_bundle(experimenter=good)).experimenter == good
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(_bundle(experimenter="e" * 65))


def test_a_request_without_the_new_fields_reads_as_before():
    bundle = MetadataBundle.model_validate(
        _bundle(runs=[_run_entry()], subject_details={"species": "Macaca mulatta"}))
    assert bundle.runs[0].marked_bad is False and bundle.runs[0].bad_reason is None
    assert bundle.subject_details.nickname is None


# --- A listed run, block or file never ends before it starts. ---------------------------------------
# Design spec `2026-11-24-wl-works-requests-design.md` section 4.5: the backstop behind `build_entry`.


def test_listed_items_refuse_an_end_before_the_start():
    from wl_preproc.contracts.protocol import ListedBlock, ListedRun, ListedSegment

    items = {
        ListedSegment: {"segment_id": "a.nidq.bin", "probes": None},
        ListedRun: {"run_number": 3, "task_code": 0, "task": None, "closed": True, "stopped_because": None,
                    "stop_kind": None, "calibration": None, "segments": [], "blocks": []},
        ListedBlock: {"block_number": 4, "block_in_run": 1, "block_type": None, "closed": None, "n_trials": 0},
    }
    for model, fields in items.items():
        with pytest.raises(ValidationError, match="ends at 4.999 s, before its start at 5.0 s") as excinfo:
            model.model_validate({**fields, "start_s": 5.0, "end_s": 4.999})
        assert excinfo.value.errors()[0]["type"] == "ends_before_start"
        assert model.model_validate({**fields, "start_s": 5.0, "end_s": 5.0}).end_s == 5.0
        assert model.model_validate({**fields, "start_s": 5.0, "end_s": 6.0}).end_s == 6.0


def test_a_listed_flag_may_name_a_file():
    from wl_preproc.contracts.protocol import ListedFlag

    flag = ListedFlag.model_validate({"code": "ends_before_start", "message": "m", "segment_id": "a.nidq.bin"})
    assert (flag.segment_id, flag.run_number, flag.block_number) == ("a.nidq.bin", None, None)
    assert ListedFlag.model_validate({"code": "task_unknown", "message": "m"}).segment_id is None


def test_idempotency_key_is_one_to_sixty_four_characters():
    """`request.Request.idempotency_key` is varchar(64); wl.works' own are `wlw-act-` plus a uuid (44)."""
    def request(key):
        return JobRequest(domain="neural", selection={}, parameters={}, idempotency_key=key,
                          metadata=MetadataBundle(montage_boundaries=[], runs=[], probes=[], experimenter="jw",
                                                  subject="s", task_types=[]))

    for refused in ("", "k" * 65):
        with pytest.raises(ValidationError):
            request(refused)
    for accepted in ("wlw-act-" + "0123456789abcdef0123456789abcdef0123", "k" * 64, "k"):
        assert request(accepted).idempotency_key == accepted


# -- PUT /positions' body ------------------------------------------------------


def _at(hhmm, second=0):
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.datetime(2026, 11, 20, hour, minute, second)


def _position(number=1, serial="S1", start="09:00", end=None, kind=None, **extra):
    body = {"serial": serial, "insertion_number": number, "inserted_at": _at(start)}
    if end is not None:
        body["ended_at"] = _at(end)
    if kind is not None:
        body["end_kind"] = kind
    body.update(extra)
    return body


def _positions(*positions, seq=3):
    return {
        "subject": "M1",
        "session_datetime": _at("08:30"),
        "positions_seq": seq,
        "positions": list(positions),
    }


def test_a_position_set_round_trips():
    body = _positions(
        _position(1, start="09:00", end="09:05", kind="moved"),
        _position(2, start="09:05"),
    )
    request = PositionsRequest.model_validate(body)
    assert request.positions_seq == 3
    assert request.model_dump(mode="python") == PositionsRequest.model_validate(request.model_dump()).model_dump()
    dumped = request.model_dump()
    assert dumped["positions"][0]["end_kind"] == "moved"
    assert dumped["positions"][1]["ended_at"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"ended_at": _at("09:05")},
        {"end_kind": "withdrawn"},
        {"ended_after_run": 2},
    ],
)
def test_an_end_and_its_kind_come_together(extra):
    with pytest.raises(ValidationError) as caught:
        PositionsRequest.model_validate(_positions(_position(7, **extra)))
    assert "insertion_number 7" in str(caught.value)
    assert "ended_at" in str(caught.value) or "end_kind" in str(caught.value)


def test_a_position_ends_no_earlier_than_it_starts():
    with pytest.raises(ValidationError) as caught:
        ProbePosition.model_validate(
            {
                "serial": "S1",
                "insertion_number": 4,
                "inserted_at": _at("09:05"),
                "ended_at": _at("09:04", 59),
                "end_kind": "withdrawn",
            }
        )
    assert "insertion_number 4" in str(caught.value)
    equal = ProbePosition.model_validate(
        {
            "serial": "S1",
            "insertion_number": 4,
            "inserted_at": _at("09:05"),
            "ended_at": _at("09:05"),
            "end_kind": "withdrawn",
        }
    )
    assert equal.ended_at == equal.inserted_at
    # A naive start and an aware end compare as UTC instants: no TypeError.
    aware = datetime.datetime(2026, 11, 20, 9, 6, tzinfo=datetime.timezone.utc)
    mixed = ProbePosition.model_validate(
        {
            "serial": "S1",
            "insertion_number": 4,
            "inserted_at": _at("09:05"),
            "ended_at": aware,
            "end_kind": "moved",
        }
    )
    assert mixed.ended_at == aware
    early = datetime.datetime(2026, 11, 20, 9, 4, tzinfo=datetime.timezone.utc)
    with pytest.raises(ValidationError):
        ProbePosition.model_validate(
            {
                "serial": "S1",
                "insertion_number": 4,
                "inserted_at": _at("09:05"),
                "ended_at": early,
                "end_kind": "moved",
            }
        )


def test_one_insertion_number_once():
    with pytest.raises(ValidationError) as caught:
        PositionsRequest.model_validate(_positions(_position(2, "S1"), _position(2, "S2")))
    assert "insertion_number 2 is given twice" in str(caught.value)
    ok = PositionsRequest.model_validate(_positions(_position(1, "S1"), _position(3, "S2"), _position(4, "S3")))
    assert [p.insertion_number for p in ok.positions] == [1, 3, 4]


def test_one_serials_positions_do_not_overlap():
    with pytest.raises(ValidationError) as caught:
        PositionsRequest.model_validate(
            _positions(
                _position(1, start="09:00", end="09:10", kind="moved"),
                _position(2, start="09:05"),
            )
        )
    message = str(caught.value)
    assert "insertion_number 1" in message and "insertion_number 2" in message
    PositionsRequest.model_validate(
        _positions(
            _position(1, start="09:00", end="09:05", kind="moved"),
            _position(2, start="09:05"),
        )
    )
    # Two serials at the same time are not one serial overlapping itself.
    PositionsRequest.model_validate(_positions(_position(1, "S1"), _position(2, "S2")))


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (_position(5, serial="x" * 33), "serial"),
        (_position(5, serial=""), "serial"),
        (_position(5, inserted_after_run=-1), "inserted_after_run"),
        (_position(5, inserted_after_run=32768), "inserted_after_run"),
        (_position(5, start="09:00", end="09:05", kind="moved", ended_after_run=-1), "ended_after_run"),
        (_position(5, start="09:00", end="09:05", kind="moved", ended_after_run=32768), "ended_after_run"),
        (_position(5, end_kind="sideways"), "end_kind"),
    ],
)
def test_the_bounds_refused(body, field):
    with pytest.raises(ValidationError) as caught:
        PositionsRequest.model_validate(_positions(body))
    message = str(caught.value)
    assert "insertion_number 5" in message
    assert field in message
    assert {error["type"] for error in caught.value.errors()}.isdisjoint({"value_error"})


@pytest.mark.parametrize("number", [0, 256, None])
def test_an_unusable_number_is_refused_as_pydantic_words_it(number):
    body = _position(1)
    if number is None:
        del body["insertion_number"]
    else:
        body["insertion_number"] = number
    with pytest.raises(ValidationError) as caught:
        PositionsRequest.model_validate(_positions(body))
    assert "insertion_number" in str(caught.value)


def test_the_bounds():
    with pytest.raises(ValidationError):
        PositionsRequest.model_validate(_positions(_position(1), seq=-1))
    ok = PositionsRequest.model_validate(_positions(_position(1, inserted_after_run=0), seq=0))
    assert ok.positions[0].inserted_after_run == 0


def test_a_positions_refusal_is_a_custom_error():
    bodies = [
        _positions(_position(1, ended_at=_at("09:05"))),
        _positions(_position(1, end_kind="moved")),
        _positions(_position(1, ended_after_run=1)),
        _positions(_position(1, start="09:05", end="09:04", kind="moved")),
        _positions(_position(2, "S1"), _position(2, "S2")),
        _positions(
            _position(1, start="09:00", end="09:10", kind="moved"),
            _position(2, start="09:05"),
        ),
    ]
    for body in bodies:
        with pytest.raises(ValidationError) as caught:
            PositionsRequest.model_validate(body)
        assert {error["type"] for error in caught.value.errors()}.isdisjoint({"value_error"})


def test_as_naive_utc_converts_without_truncating():
    naive = datetime.datetime(2026, 11, 20, 9, 0, 0, 500)
    assert as_naive_utc(naive) is naive
    plus_one = datetime.datetime(
        2026,
        11,
        20,
        10,
        0,
        0,
        500,
        tzinfo=datetime.timezone(datetime.timedelta(hours=1)),
    )
    assert as_naive_utc(plus_one) == naive
    assert as_naive_utc(plus_one).tzinfo is None

"""`docs/ops/lab-host-protocol.md` is the wire contract wl.works builds
against, and keeps a copy of: what the listing and the NWB description carry
is named there. The listing and the description gained wl-xcon's record of the
session without it (design spec `2026-10-14-session-kind-and-warnings-design.md`,
the final review's I1), so the names are checked here."""

from __future__ import annotations

import pathlib

PROTOCOL = pathlib.Path(__file__).resolve().parents[2] / "docs" / "ops" / "lab-host-protocol.md"


def _section(heading: str) -> str:
    text = PROTOCOL.read_text(encoding="utf-8")
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end]


def test_every_field_a_listed_session_carries_is_named_in_the_protocol():
    from wl_preproc.contracts.protocol import (ListedActor, ListedCalibration, ListedMontage, ListedMontageProbe,
                                               ListedRun, ListedSharedGroup, ListedTrainingRecords, ListedWarning,
                                               SessionEntry)

    section = _section("`GET /sessions`")
    missing = sorted(f"{model.__name__}.{name}"
                     for model in (SessionEntry, ListedRun, ListedWarning, ListedCalibration, ListedActor,
                                   ListedTrainingRecords, ListedMontage, ListedMontageProbe, ListedSharedGroup)
                     for name in model.model_fields if f"`{name}`" not in section)
    assert missing == []


def test_the_descriptions_record_of_the_session_is_named_in_the_protocol():
    from wl_preproc.contracts.nwb_description import AcceptedWarning, RigCalibration, TrainingRecords

    section = _section("`GET /nwb`")
    names = ["session_kind", "calibration", "accepted_warnings", "training_records",
             *AcceptedWarning.model_fields, *RigCalibration.model_fields, *TrainingRecords.model_fields]
    assert [name for name in names if f"`{name}`" not in section] == []


def test_a_rig_record_problem_is_said_to_be_about_the_rigs_whole_record():
    """Since the record is more than the runs, each problem names its file
    (spec amendment 2)."""
    section = _section("`GET /sessions`")
    assert "what wl-xcon's run record could not supply" not in section
    assert "`runs.jsonl`" in section and "`config.json`" in section and "`warnings.jsonl`" in section


def test_the_404_row_names_every_path_and_both_query_paths():
    """The router answers 404 for a path that is not one of the five, and for a query string on any path but
    `/nwb` and `/sessions` (`handler.py::_CURSOR_PATHS`); the status table said `/nwb` only and left out
    `/sessions` (design spec `2026-11-24-wl-works-requests-design.md` section 2.7)."""
    text = PROTOCOL.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| `404` |"))
    for path in ("/health", "/jobs", "/nwb", "/nwb/active", "/positions", "/sessions"):
        assert f"`{path}`" in row
    assert "a query string on any path but `/nwb` and `/sessions`" in row


def test_every_field_positions_carry_is_named_in_the_protocol():
    from wl_preproc.contracts.protocol import PositionsRequest, PositionsResponse, ProbePosition

    section = _section("`PUT /positions`")
    missing = sorted(f"{model.__name__}.{name}"
                     for model in (PositionsRequest, ProbePosition, PositionsResponse)
                     for name in model.model_fields if f"`{name}`" not in section)
    assert missing == []


def test_the_shared_role_and_the_retired_boundaries_are_documented():
    """The montages piece (`2026-11-24-montages-design.md` section 4.6): `montage_boundaries` is retired, and a
    request may name a shared group. wl.works builds its request from this section, and its 409 and 404 rows
    answer `PUT /positions` too."""
    section = _section("`POST /jobs`")
    assert "`shared`" in section and "`group_id`" in section
    assert "`montage_boundaries` is retired" in section
    text = PROTOCOL.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| `409` |")]
    assert any("`PUT /positions`" in row for row in rows)
    assert "(Task 12" not in text
    assert "recorded **if absent**" not in text and "records it **if absent**" not in text

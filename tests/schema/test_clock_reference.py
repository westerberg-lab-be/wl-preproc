"""The wall-clock time of session t = 0, worked out once by the daemon with
`timebase/clock.py::Clock.wall_reference` and stored (`timebase.ClockReference`),
so that `GET /sessions` lists it and the NWB build reads it (the requester's
choice of 2026-11-24: wl.works places a person's minute-level move times
between runs, which `session_datetime`'s up to 60 s cannot). Every session here
is generated, hand-landed and taken through one daemon pass."""

from __future__ import annotations

import datetime

import pytest

from tests.identities import new_animal

_UTC = datetime.timezone.utc


def _naive_utc(value: datetime.datetime) -> datetime.datetime:
    return value.astimezone(_UTC).replace(tzinfo=None)


@pytest.fixture(scope="module")
def landed(dj_conn, prefix, tmp_path_factory):
    """Each session by name, `(key, session_dir, first barcode value)`, after
    one daemon pass: a sync box session whose manifest agrees with its first
    barcode (`barcode`); an NI session dated at the synthetic rig's own clock,
    12.5 s from its fit (`rig_clock`); and an NI session dated years from both
    the rig's clock and any NI file (`manifest`). Deleted afterwards."""
    from tests.schema.test_eye_populate import _land
    from wl_preproc import daemon
    from wl_preproc.schema import pipeline
    from wl_preproc.synth.recipe import CI_RECIPE, NI_CLOCK_RECIPE, SYNTH_EPOCH
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase.clock import BARCODE_EPOCH
    from wl_preproc.timebase.segments import session_reference

    daemon.activate_all(prefix=prefix)
    sessions = {}

    def land(name: str, recipe, when):
        animal = new_animal()
        session = animal.session()
        recipe = recipe.model_copy(update={"subject": session.subject, "session_id": session.session_id})
        root = tmp_path_factory.mktemp(name)
        generate_session(root, recipe)
        session_dir = root / recipe.session_id
        reference = session_reference(session_dir) if "syncbox" in recipe.systems else {}
        first = min(reference, key=reference.get) if reference else None
        moment = when(first) if when is not None else session.session_datetime
        key = _land(root, recipe, moment, acquisition_systems=recipe.systems)
        sessions[name] = (key, session_dir, first)

    land("barcode", CI_RECIPE.model_copy(update={"systems": ("syncbox", "spikeglx"), "runs": True}),
         lambda first: _naive_utc(BARCODE_EPOCH + datetime.timedelta(seconds=first + 5)))
    land("rig_clock", NI_CLOCK_RECIPE, lambda _first: _naive_utc(SYNTH_EPOCH))
    land("manifest", NI_CLOCK_RECIPE, None)
    report = daemon.run_once(prefix=prefix)
    yield sessions, report
    for key, _session_dir, _first in sessions.values():
        (pipeline.Session & key).delete(prompt=False)


def _stored(key) -> dict:
    from wl_preproc.schema import timebase

    return (timebase.ClockReference & key).fetch1()


def test_a_sync_box_session_stores_its_first_barcodes_time(landed):
    from wl_preproc.timebase.clock import BARCODE_EPOCH

    sessions, report = landed
    key, _session_dir, first = sessions["barcode"]
    row = _stored(key)
    assert row["reference_source"] == "barcode", report["errors"]
    assert row["reference_time"] == _naive_utc(BARCODE_EPOCH + datetime.timedelta(seconds=first))
    assert row["started_at_difference_s"] == pytest.approx(5.0, abs=1e-9)


def test_an_ni_session_with_a_rig_clock_fit_stores_the_fits_time(landed):
    from wl_preproc.schema import core

    sessions, report = landed
    key, _session_dir, _first = sessions["rig_clock"]
    row = _stored(key)
    assert row["reference_source"] == "rig_clock", report["errors"]
    rig_at_zero = (core.RigClock & key).fetch1("rig_at_zero")
    # To the microsecond: the column is datetime(6), and the fit's time is
    # not a whole second.
    assert row["reference_time"] == _naive_utc(datetime.datetime.fromtimestamp(rig_at_zero, tz=_UTC))
    assert row["reference_time"].microsecond != 0
    assert row["started_at_difference_s"] == pytest.approx(
        key["session_datetime"].replace(tzinfo=_UTC).timestamp() - rig_at_zero, abs=1e-6)


def test_an_ni_session_far_from_every_source_stores_the_manifests_time(landed):
    from wl_preproc.schema import core

    sessions, report = landed
    key, _session_dir, _first = sessions["manifest"]
    row = _stored(key)
    assert row["reference_source"] == "manifest", report["errors"]
    assert row["reference_time"] == key["session_datetime"]
    rig_at_zero = (core.RigClock & key).fetch1("rig_at_zero")
    assert row["started_at_difference_s"] == pytest.approx(
        key["session_datetime"].replace(tzinfo=_UTC).timestamp() - rig_at_zero, abs=1e-6)


def test_the_listing_carries_the_stored_value_and_its_digest_follows_it(landed):
    """The entry lists the stored row, microseconds kept, and the listing
    stage's digest changes when the stored value does, so wl.works is told."""
    from wl_preproc.listing.entry import session_entry
    from wl_preproc.listing.stage import digest
    from wl_preproc.schema import timebase

    sessions, _report = landed
    key, _session_dir, _first = sessions["rig_clock"]
    row = _stored(key)
    entry = session_entry(key)
    assert (entry["t0_datetime"], entry["t0_source"]) == (row["reference_time"].isoformat(), "rig_clock")
    before = digest(entry)
    moved = row["reference_time"] + datetime.timedelta(microseconds=7)
    timebase.ClockReference.update1({**key, "reference_time": moved})
    try:
        entry = session_entry(key)
        assert entry["t0_datetime"] == moved.isoformat()
        assert digest(entry) != before
    finally:
        timebase.ClockReference.update1({**key, "reference_time": row["reference_time"]})


def test_the_build_waits_for_the_stored_value(landed):
    """A session whose t = 0 is not stored yet is not built: `readiness`
    names the wait, as for every other stage the file reads."""
    from wl_preproc.nwb.gather import readiness
    from wl_preproc.schema import timebase

    sessions, _report = landed
    key, _session_dir, _first = sessions["manifest"]
    row = _stored(key)
    activation = {**key, "montage_id": 0, "activation_id": 0}
    (timebase.ClockReference & key).delete_quick()
    try:
        assert readiness(activation).startswith("waiting on ClockReference")
    finally:
        timebase.ClockReference.insert1(row, allow_direct_insert=True)

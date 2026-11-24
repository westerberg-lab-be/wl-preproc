"""Probes and areas in the NWB file, through the builder (design specs
`2026-09-30-nwb-probes-design.md` sections 3.1, 3.3 and 5, and
`2026-11-24-montages-design.md` sections 4.3 and 4.7): each file takes its
probes' insertions and site maps from the montage it is built for, which the
montage stage derives from the positions wl.works sends. Subjects, dates and
serials written by hand were checked unclaimed across `tests/` on 2026-09-30;
newer tests take theirs from `tests/identities.py`. The dates are in the PAST,
since `nwbinspector` calls a future `session_start_time` critical."""

from __future__ import annotations

import datetime

import pytest

from tests.identities import new_animal, new_serial
from tests.positions import before_t0, put
from tests.schema.test_spikeglx_restart import RESTART
from tests.schema.test_spikeglx_restart import _session as _restart_session


def _session(tmp_path_factory, mutate=None, **update):
    """`test_spikeglx_restart.py::_session`, its blocks wrapped in runs: a
    canonical request names the session's measured runs (design spec
    `2026-10-01-session-listing-and-run-requests-design.md` section 3)."""
    return _restart_session(tmp_path_factory, mutate, runs=True, **update)

_S1, _S2, _S3 = "19011110011", "19011110012", "19011110013"
_AIM = {"area": "V4d", "atlas": "CHARM", "atlas_level": 6}
_V1 = {"area": "V1", "atlas": "CHARM", "atlas_level": 6}
_ASSIGNED = {"area": "V4v", "source": "at_rig", "asserted_at": "2025-06-22T11:00:00Z"}


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def _bad_part(directory):
    """imec2's probe becomes a type probeinterface does not know."""
    for meta in directory.glob("*imec2.ap.meta"):
        meta.write_text(meta.read_text().replace("imDatPrb_pn=NP1000", "imDatPrb_pn=NP9999"))


def _position(serial, number, inserted_at, **more):
    return {"serial": serial, "insertion_number": number, "inserted_at": inserted_at, **more}


def _positioned(daemon_module, prefix, key, positions):
    """wl.works' `PUT /positions` for the session, then a pass: the montage
    stage derives its montages and shared groups from them. Call it after the
    session's first pass, which stores the `ClockReference` `before_t0` reads."""
    from wl_preproc.schema import core

    put(key, positions, prefix=prefix)
    report = daemon_module.run_once(prefix=prefix)
    assert not [error for error in report["errors"] if key["subject"] in error]
    assert (core.MontageDerivation & key).fetch1("positions_status") == "placed"


def _position_each_probe(daemon_module, prefix, key):
    """`_positioned` with one position for each serial the session's census
    names, numbered from 1 in serial order, each over the whole session: what
    a test whose subject is not its probes needs before `_canonical`."""
    from wl_preproc.schema import ephys

    serials = sorted({serial for serial in (ephys.ProbeCensus.Probe & key).to_arrays("probe_serial") if serial})
    _positioned(daemon_module, prefix, key,
                [_position(serial, number, before_t0(key)) for number, serial in enumerate(serials, start=1)])


def _canonical(daemon_module, prefix, key, idempotency_key, probes, run_numbers=None, montage_id=1):
    """wl.works' canonical over montage `montage_id`'s measured runs, as
    `tests/schema/test_nwb_build.py`'s fixture asks for it, each probe's sort
    covering its sortable runs there as this host lists them, then a pass.
    With `run_numbers`, a derivative over those runs instead. The montage is
    the stage's, from the session's stored positions: call `_positioned` (or
    `_position_each_probe`) first."""
    from wl_preproc.contracts.protocol import JobRequest, MetadataBundle
    from wl_preproc.montage.stored import read_stored
    from wl_preproc.responder.jobs import accept
    from wl_preproc.schema import core

    runs = [{"run_number": row["run_number"], "start_s": row["run_start_time"], "end_s": row["run_stop_time"],
             "works_run_id": f"wr-{row['run_number']}"}
            for row in (core.Run & key).to_dicts(order_by="run_number")]
    (stored,) = read_stored([key]).values()
    (montage,) = [montage for montage in stored.montages if montage["montage_id"] == montage_id]
    sortable = {probe["serial"]: list(probe["sortable_runs"]) for probe in montage["probes"]}
    every = {probe["serial"]: sortable.get(probe["serial"], []) for probe in probes}
    activation = accept(JobRequest(
        domain="neural", parameters={}, idempotency_key=idempotency_key,
        selection={"session_datetime": key["session_datetime"].replace(tzinfo=datetime.UTC),
                   "montage_id": montage_id,
                   **({"probe_runs": every} if run_numbers is None else {"run_numbers": run_numbers})},
        metadata=MetadataBundle(
            blocks=[], runs=runs, probes=probes, experimenter="jw", subject=key["subject"], task_types=[],
            subject_details={"species": "Macaca mulatta", "sex": "F", "date_of_birth": datetime.date(2016, 3, 2)}),
    ), prefix=prefix)
    daemon_module.run_once(prefix=prefix)
    return activation


def _new_session(tmp_path_factory, mutate=None, **update):
    """`_session` for a new animal, its `imec0` a new serial unless `update` names one."""
    session = new_animal().session()
    update.setdefault("probe_serial", new_serial())
    return _session(tmp_path_factory, mutate, subject=session.subject, session_id=session.session_id,
                    session_datetime=session.session_datetime, **update)


@pytest.fixture(scope="module")
def three_probes(daemon_module, prefix, tmp_path_factory):
    """Three probes, one per area label: imec0 has an aim and an
    assignment, imec1 an aim only, and imec2 no position, so no report, and
    a part number probeinterface does not know. A restart that keeps every
    bank makes two segments with one map each."""
    _recipe, key = _session(
        tmp_path_factory, _bad_part, subject="pnwb1", session_id="2025-06-22_01", probe_serial=_S1,
        extra_probes=[{"serial": _S2, "part_number": "NP1032", "bank": 1}, {"serial": _S3}],
        spikeglx_restart=RESTART)
    daemon_module.run_once(prefix=prefix)
    # A typo: no recording in the session names 19011119998.
    _positioned(daemon_module, prefix, key, [_position(_S1, 1, before_t0(key)), _position(_S2, 2, before_t0(key)),
                                             _position("19011119998", 4, before_t0(key))])
    activation = _canonical(daemon_module, prefix, key, "pnwb1-k1", [
        {"serial": _S1, "insertion_number": 1, "trajectory_id": "T-1", "target": _AIM, "area_assignment": _ASSIGNED},
        {"serial": _S2, "insertion_number": 2, "target": _AIM},
        # A typo: no recording in the session names this serial.
        {"serial": "19011119998", "insertion_number": 4},
    ])
    return key, activation


@pytest.fixture(scope="module")
def built(three_probes, tmp_path_factory):
    from wl_preproc.nwb.build import build

    _key, activation = three_probes
    return build(activation, tmp_path_factory.mktemp("nwb-probes"))


def test_each_probe_is_a_device_named_for_its_serial(built):
    from pynwb import NWBHDF5IO

    assert built.status == "written", (built.reason, built.findings)
    with NWBHDF5IO(str(built.path), "r") as handle:
        nwb = handle.read()
        devices = {name: (device.serial_number, device.model.name) for name, device in nwb.devices.items()}
        assert devices == {f"probe-{_S1}": (_S1, "NP1000"), f"probe-{_S2}": (_S2, "NP1032"),
                           f"probe-{_S3}": (_S3, "NP9999")}
        groups = {name: (group.device.name, group.location) for name, group in nwb.electrode_groups.items()}
        assert groups == {"insertion-1": (f"probe-{_S1}", "V4v"), "insertion-2": (f"probe-{_S2}", "V4d"),
                          f"probe-{_S3}": (f"probe-{_S3}", "unknown")}
        described = nwb.electrode_groups["insertion-1"].description
        assert "target: V4d (CHARM, level 6)" in described and "assigned at rig: V4v (2025-06-22)" in described


def test_every_active_site_is_one_row_carrying_its_probes_areas(built):
    """Once per probe, not once per segment: the restart kept the bank.
    The label is the assignment, else the aim, else `unknown`, and both
    areas stay visible on every row."""
    from pynwb import NWBHDF5IO

    from wl_preproc.ephys.geometry import electrode_rows

    with NWBHDF5IO(str(built.path), "r") as handle:
        electrodes = handle.read().electrodes
        table = electrodes.to_dataframe()
        described = {column: electrodes[column].description for column in ("target_area", "assigned_area")}
    rows = {group: frame for group, frame in table.groupby("group_name")}
    assert set(rows) == {"insertion-1", "insertion-2"}  # imec2 has no geometry
    first, second = rows["insertion-1"], rows["insertion-2"]
    assert first["electrode"].tolist() == [0, 1, 2, 3] and second["electrode"].tolist() == [384, 385, 386, 387]
    sites = {row["electrode"]: row for row in electrode_rows("NP1032")}
    assert second["rel_x"].tolist() == [sites[e]["x_coord"] for e in range(384, 388)]
    assert second["rel_y"].tolist() == [sites[e]["y_coord"] for e in range(384, 388)]
    assert set(zip(first["location"], first["target_area"], first["assigned_area"], first["assigned_area_source"])) == {
        ("V4v", "V4d", "V4v", "at_rig")}
    assert set(zip(second["location"], second["target_area"], second["assigned_area"])) == {("V4d", "V4d", "")}
    # pynwb fixes the table's own description (NWB 2.9's ElectrodesTable),
    # so each area column says it is per insertion.
    assert all("not depth-resolved" in text for text in described.values())


def test_a_new_form_header_places_the_same_sites_in_the_file(daemon_module, prefix, tmp_path_factory):
    """SpikeGLX 20260115 on names the part number in the `~imroTbl` header
    (design spec `2026-11-19-spikeglx-pn-header-design.md`): the file's
    electrodes are the sites the old form places, NP1032's bank 2 among
    them, with nothing noted."""
    from pynwb import NWBHDF5IO

    from wl_preproc.ephys.geometry import electrode_rows
    from wl_preproc.nwb.build import build

    _recipe, key = _session(tmp_path_factory, subject="pnwb8", session_id="2025-06-30_01", probe_serial="19011110031",
                            probe_part_number="NP1032", probe_bank=2, imro_header="part_number",
                            extra_probes=[{"serial": "19011110032", "bank": 1}])
    daemon_module.run_once(prefix=prefix)
    _position_each_probe(daemon_module, prefix, key)
    activation = _canonical(daemon_module, prefix, key, "pnwb8-k1", [])

    result = build(activation, tmp_path_factory.mktemp("nwb-new-form"))
    assert result.status == "written", (result.reason, result.findings)
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        table = nwb.electrodes.to_dataframe()
        described = nwb.session_description + " " + (nwb.experiment_description or "")
    by_serial = {}
    for device_name, frame in table.groupby(table["group"].map(lambda group: group.device.name)):
        by_serial[device_name] = frame
    assert sorted(by_serial) == ["probe-19011110031", "probe-19011110032"]
    nhp = by_serial["probe-19011110031"]
    assert nhp["electrode"].tolist() == [768, 769, 770, 771]
    sites = {row["electrode"]: row for row in electrode_rows("NP1032")}
    assert nhp["rel_x"].tolist() == [sites[e]["x_coord"] for e in range(768, 772)]
    assert by_serial["probe-19011110032"]["electrode"].tolist() == [384, 385, 386, 387]
    assert "could not be mapped" not in described and "header names" not in described


def test_an_intan_probe_is_listed_from_its_report_alone(daemon_module, prefix, tmp_path_factory):
    """Section 5: an RHS header does not name its probe, so wl.works' report
    is the only record of it. It is listed with no type and no electrodes,
    and the file says why. Landed by hand with `syncbox` and `rhs`:
    `_session` declares SpikeGLX."""
    from pynwb import NWBHDF5IO

    from tests.schema.test_eye_populate import _land
    from wl_preproc.nwb.build import build
    from wl_preproc.nwb.gather import gather
    from wl_preproc.synth.recipe import CI_RECIPE
    from wl_preproc.synth.session import generate_session

    recipe = CI_RECIPE.model_copy(update={"subject": "pnwb4", "session_id": "2025-06-25_01",
                                          "systems": ("syncbox", "rhs"), "runs": True})
    root = tmp_path_factory.mktemp("pnwb4")
    generate_session(root, recipe)
    key = _land(root, recipe, datetime.datetime(2025, 6, 25, 9), acquisition_systems=("syncbox", "rhs"))
    daemon_module.run_once(prefix=prefix)
    _positioned(daemon_module, prefix, key, [_position("R-19", 1, before_t0(key))])
    activation = _canonical(daemon_module, prefix, key, "pnwb4-k1",
                            [{"serial": "R-19", "insertion_number": 1, "target": _AIM}])

    assert gather(activation).probe_notes == [
        "probe R-19 (insertion 1) is listed from wl.works' report alone: no SpikeGLX recording names it, and an "
        "Intan (RHS) header does not name its probe"]
    result = build(activation, tmp_path_factory.mktemp("nwb-intan"))
    assert result.status == "written", (result.reason, result.findings)
    with NWBHDF5IO(str(result.path), "r") as handle:
        nwb = handle.read()
        assert (nwb.devices["probe-R-19"].serial_number, nwb.devices["probe-R-19"].model) == ("R-19", None)
        assert nwb.electrode_groups["insertion-1"].location == "V4d" and nwb.electrodes is None


def _no_serial(directory):
    for meta in directory.glob("*_imec0.ap.meta"):
        meta.write_text("".join(line for line in meta.read_text().splitlines(keepends=True)
                                if not line.startswith("imDatPrb_sn=")))


def test_a_meta_naming_no_serial_is_noted_by_its_segment(daemon_module, prefix, tmp_path_factory):
    """Section 5: the probe cannot be named, so it is not listed; the note
    says which segment's stream it was."""
    from wl_preproc.nwb.gather import gather

    recipe, key = _session(tmp_path_factory, _no_serial, subject="pnwb5", session_id="2025-06-26_01")
    daemon_module.run_once(prefix=prefix)
    # The census names no serial, so wl.works' set is empty.
    _position_each_probe(daemon_module, prefix, key)
    activation = _canonical(daemon_module, prefix, key, "pnwb5-k1", [])

    data = gather(activation)
    assert data.probes == []
    assert data.probe_notes == [f"{recipe.session_id}.nidq.bin imec0: the .meta names no serial (imDatPrb_sn); the "
                                "probe it recorded is unknown"]


def test_an_imro_table_too_long_to_keep_is_not_a_probe_note(daemon_module, prefix, tmp_path_factory, monkeypatch):
    """The session listing's M6: the census's note that a probe's imroTbl was
    too long to keep stays in the census, for the listing, and stays out of
    the file. Its sites are read from the .meta's whole table all the same,
    so nothing is wrong with the probe. No lab probe's table comes near the
    width kept, and the synthetic table records four channels, so the census
    pass runs with a width shorter than any table."""
    from wl_preproc.nwb.gather import gather
    from wl_preproc.schema import ephys

    _recipe, key = _session(tmp_path_factory, subject="pnwb7", session_id="2025-06-20_01", probe_serial="19011110020")
    with monkeypatch.context() as narrower:
        narrower.setattr(ephys, "IMRO_TABLE_MAX", 10)
        daemon_module.run_once(prefix=prefix)
    parts = (ephys.ProbeCensus.Probe & key).to_dicts()
    assert parts and all(part["problem"].endswith("so it is not kept") and part["electrode_config_hash"]
                         for part in parts)
    _position_each_probe(daemon_module, prefix, key)
    activation = _canonical(daemon_module, prefix, key, "pnwb7-k1",
                            [{"serial": "19011110020", "insertion_number": 1, "target": _AIM}])

    data = gather(activation)
    assert [probe["serial"] for probe in data.probes] == ["19011110020"]
    assert data.probe_notes == []


_BANKED = "19011110014"


def _bank_note(serial, run, montage_id, ending=None):
    return (f"probe {serial} records two site maps under this file's runs, the change inside run {run}: the file "
            f"gives it the site map its sortable runs in montage {montage_id} share"
            + (f", and run {run} is not sorted for it" if ending is None else ending))


@pytest.fixture(scope="module")
def bank_change(daemon_module, prefix, tmp_path_factory):
    """SpikeGLX restarts at 11 s, inside run 2, with another bank (design
    spec `2026-11-24-montages-design.md` section 4.3): one montage over both
    runs, in which the probe's one sortable run is run 1, so the montage lists
    the site map of the first segment, bank 0's. Returns the session's key
    and its canonical, whose report names the probe."""
    _recipe, key = _session(tmp_path_factory, subject="pnwb2", session_id="2025-06-23_01", probe_serial=_BANKED,
                            spikeglx_restart={**RESTART, "probe_bank": 1})
    daemon_module.run_once(prefix=prefix)
    _positioned(daemon_module, prefix, key, [_position(_BANKED, 1, before_t0(key))])
    return key, _canonical(daemon_module, prefix, key, "pnwb2-k1",
                           [{"serial": _BANKED, "insertion_number": 1, "target": _AIM}])


def _electrodes(result):
    from pynwb import NWBHDF5IO

    with NWBHDF5IO(str(result.path), "r") as handle:
        return handle.read().electrodes.to_dataframe()["electrode"].tolist()


def test_a_bank_change_inside_a_run_builds_with_the_sortable_site_map(bank_change, tmp_path_factory):
    """Section 4.3: the canonical of a montage holding the run is built, no
    longer refused, with the site map run 1 was recorded through, and its
    notes name run 2 as the one where the map changed."""
    from wl_preproc.nwb.build import build

    _key, canonical = bank_change
    result = build(canonical, tmp_path_factory.mktemp("nwb-bank-canonical"))

    assert result.status == "written", (result.reason, result.findings)
    assert _electrodes(result) == [0, 1, 2, 3]
    assert _bank_note(_BANKED, 2, 1) in result.description["notes"]
    (probe,) = result.description["probes"]
    assert (probe["insertion_number"], probe["sorted_runs"]) == (1, [1])


def test_a_file_on_one_side_of_a_bank_change_builds(daemon_module, prefix, bank_change, tmp_path_factory):
    """The final review's I1: a file's site maps come from the segments its
    own runs overlap, not from its whole montage. A derivative of run 1 is
    recorded through one site map, and says nothing of the change."""
    from wl_preproc.nwb.build import build

    key, _canonical_key = bank_change
    before = _canonical(daemon_module, prefix, key, "pnwb2-k2", [], run_numbers=[1])

    result = build(before, tmp_path_factory.mktemp("nwb-bank-before"))
    assert result.status == "written", (result.reason, result.findings)
    assert _electrodes(result) == [0, 1, 2, 3]
    assert not [note for note in result.description["notes"] if "two site maps" in note]


def test_a_derivative_across_the_change_builds_too(daemon_module, prefix, bank_change, tmp_path_factory):
    """Run 2 spans both segments, so its derivative's probe records two site
    maps; it carries the montage's, and the canonical's note."""
    from wl_preproc.nwb.build import build

    key, _canonical_key = bank_change
    across = _canonical(daemon_module, prefix, key, "pnwb2-k3", [], run_numbers=[2])

    result = build(across, tmp_path_factory.mktemp("nwb-bank-across"))
    assert result.status == "written", (result.reason, result.findings)
    assert _electrodes(result) == [0, 1, 2, 3]
    assert _bank_note(_BANKED, 2, 1) in result.description["notes"]


def test_a_probe_with_no_sortable_run_in_its_montage_has_no_electrodes(daemon_module, prefix, tmp_path_factory):
    """Section 4.3: SpikeGLX restarts at 4 s, inside run 1, with another
    bank, so montage 1 is run 1 alone and the probe sorts no run in it: its
    canonical lists the probe with no electrodes, and says why. Montage 2's
    file has bank 1's sites, with nothing to say."""
    from wl_preproc.nwb.gather import gather

    serial = new_serial()
    _recipe, key = _new_session(tmp_path_factory, probe_serial=serial,
                                spikeglx_restart={**RESTART, "at_s": 4.0, "probe_bank": 1})
    daemon_module.run_once(prefix=prefix)
    _positioned(daemon_module, prefix, key, [_position(serial, 1, before_t0(key))])

    first = gather(_canonical(daemon_module, prefix, key, f"{key['subject']}-m1", []))
    (probe,) = first.probes
    assert (probe["insertion_number"], probe["electrodes"]) == (1, [])
    assert _bank_note(serial, 1, 1, "; it has no sortable run in montage 1, so it has no electrodes in this file") \
        in first.probe_notes
    second = gather(_canonical(daemon_module, prefix, key, f"{key['subject']}-m2", [], montage_id=2))
    (probe,) = second.probes
    assert [electrode["electrode"] for electrode in probe["electrodes"]] == [384, 385, 386, 387]
    assert not [note for note in second.probe_notes if "two site maps" in note]


def test_a_file_waits_for_the_census_of_its_session(three_probes):
    """Section 3.3: readiness names the stage it waits on, and a session
    with no SpikeGLX segment has nothing to wait for."""
    from wl_preproc.nwb.gather import readiness
    from wl_preproc.schema import ephys

    key, activation = three_probes
    assert readiness(activation) is None
    last = (ephys.ProbeCensus & key).keys(order_by="segment_id")[-1]
    (ephys.ProbeCensus & last).delete(prompt=False)
    try:
        assert readiness(activation).startswith("waiting on ProbeCensus")
    finally:
        ephys.ProbeCensus.populate(last)
    assert readiness(activation) is None


def test_the_description_lists_each_probe_and_says_what_it_could_not_place_or_join(built):
    """Section 3.2's entries and notes: an unknown part number, a probe with
    no report, and a report that matches no recorded probe. Each probe's
    sorted runs are its list from the request; the probe with no report has
    none (design spec `2026-10-01-session-listing-and-run-requests-design.md`
    section 4)."""
    probes = built.description["probes"]
    assert [(p["serial"], p["probe_type"], p["insertion_number"], p["trajectory_id"], p["n_electrodes"],
             p["area_from"]) for p in probes] == [
        (_S1, "NP1000", 1, "T-1", 4, "assignment"), (_S2, "NP1032", 2, None, 4, "target"),
        (_S3, "NP9999", None, None, 0, "unknown")]
    assert [p["sorted_runs"] for p in probes] == [[1, 2], [1, 2], []]
    assert probes[0]["target"] == _AIM
    assert probes[0]["assignment"] == {"area": "V4v", "source": "at_rig", "asserted_at": "2025-06-22T11:00:00Z"}
    notes = built.description["notes"]
    assert any(note.startswith(f"probe {_S3}: part number NP9999") and "no electrodes in this file" in note
               for note in notes)
    assert f"probe {_S3} is named by no position from wl.works, so its insertion and area are unknown" in notes
    assert ("insertion 4 names probe 19011119998, which no recording in this session names, so the report is "
            "not joined") in notes


def test_a_moved_probe_takes_each_montages_insertion_and_area(daemon_module, prefix, tmp_path_factory):
    """Section 4.7: the probe moves after run 1 with no restart, so one
    SpikeGLX file spans the move, and each montage's file carries the
    insertion, and the area, of the position over its own runs."""
    from wl_preproc.nwb.gather import gather

    serial = new_serial()
    _recipe, key = _new_session(tmp_path_factory, probe_serial=serial)
    daemon_module.run_once(prefix=prefix)
    _positioned(daemon_module, prefix, key, [
        _position(serial, 1, before_t0(key), ended_at=before_t0(key, 0), end_kind="moved", ended_after_run=1),
        _position(serial, 2, before_t0(key, 0), inserted_after_run=1)])
    probes = [{"serial": serial, "insertion_number": 1, "target": _AIM},
              {"serial": serial, "insertion_number": 2, "target": _V1}]

    for montage_id, expected in ((1, (1, "target", "V4d")), (2, (2, "target", "V1"))):
        data = gather(_canonical(daemon_module, prefix, key, f"{key['subject']}-m{montage_id}", probes,
                                 montage_id=montage_id))
        assert [(probe["insertion_number"], probe["area_from"], probe["area"]) for probe in data.probes] == [
            expected]
        assert [len(probe["electrodes"]) for probe in data.probes] == [4]
        assert not [note for note in data.probe_notes if "reported for insertions" in note]


def test_a_recorded_probe_without_a_position_is_unknown_and_says_why(daemon_module, prefix, tmp_path_factory):
    """Three probes in one SpikeGLX file: `a` is withdrawn after run 1, `b`
    moves after run 1, and `c` is named by no position. Montage 2's file
    lists each, and says why `a` and `c` have no insertion and `b` no area:
    no request reported it."""
    from wl_preproc.nwb.gather import gather

    a, b, c = new_serial(), new_serial(), new_serial()
    _recipe, key = _new_session(tmp_path_factory, probe_serial=a,
                                extra_probes=[{"serial": b, "part_number": "NP1032", "bank": 1}, {"serial": c}])
    daemon_module.run_once(prefix=prefix)
    _positioned(daemon_module, prefix, key, [
        _position(a, 1, before_t0(key), ended_at=before_t0(key, 0), end_kind="withdrawn", ended_after_run=1),
        _position(b, 2, before_t0(key), ended_at=before_t0(key, 0), end_kind="moved", ended_after_run=1),
        _position(b, 3, before_t0(key, 0), inserted_after_run=1)])

    data = gather(_canonical(daemon_module, prefix, key, f"{key['subject']}-m2", [], montage_id=2))

    assert [(probe["serial"], probe["insertion_number"], probe["area_from"], len(probe["electrodes"]))
            for probe in data.probes] == [(b, 3, "unknown", 4), (a, None, "unknown", 0), (c, None, "unknown", 0)]
    assert data.probe_notes == [
        f"probe {a} holds no position over montage 2's runs, so its insertion and area are unknown",
        f"probe {b} (insertion 3) has no report from wl.works, so its area is unknown",
        f"probe {c} is named by no position from wl.works, so its insertion and area are unknown"]


def _shared_sentence(*groups):
    return " Electrodes it shares with other montages are sorted in shared groups: " + "; ".join(groups) + "."


def test_a_canonical_names_the_shared_groups_over_its_montage(daemon_module, prefix, tmp_path_factory):
    """Section 4.7 and plan decision D16: `other` moves after run 1, so the
    stationary probe's four electrodes are shared by montages 1 and 2, in
    group 1. Each canonical's description names it, last; a derivative's
    does not."""
    from wl_preproc.nwb.gather import gather

    serial, other = new_serial(), new_serial()
    _recipe, key = _new_session(tmp_path_factory, probe_serial=serial,
                                extra_probes=[{"serial": other, "part_number": "NP1032", "bank": 1}])
    daemon_module.run_once(prefix=prefix)
    _positioned(daemon_module, prefix, key, [
        _position(serial, 1, before_t0(key)),
        _position(other, 2, before_t0(key), ended_at=before_t0(key, 0), end_kind="moved", ended_after_run=1),
        _position(other, 3, before_t0(key, 0), inserted_after_run=1)])
    sentence = _shared_sentence(f"group 1 (probe {serial}, montages 1, 2, 4 electrodes)")

    for montage_id in (1, 2):
        data = gather(_canonical(daemon_module, prefix, key, f"{key['subject']}-m{montage_id}", [],
                                 montage_id=montage_id))
        assert data.session["description"].endswith(sentence), data.session["description"]
    derivative = gather(_canonical(daemon_module, prefix, key, f"{key['subject']}-d1", [], run_numbers=[1]))
    assert "shared groups" not in derivative.session["description"]


def test_a_group_of_a_probe_with_no_channel_map_shares_every_channel(daemon_module, prefix, tmp_path_factory):
    """Two Intan probes: `R-b` moves after run 1, so `R-a`, whose channels
    this host reads no map for, shares every channel over montages 1 and 2."""
    from tests.positions import land
    from wl_preproc.nwb.gather import gather

    session = new_animal().session()
    key = land(tmp_path_factory, systems=("syncbox", "rhs"), subject=session.subject,
               session_id=session.session_id, session_datetime=session.session_datetime)
    daemon_module.run_once(prefix=prefix)
    _positioned(daemon_module, prefix, key, [
        _position("R-a", 1, before_t0(key)),
        _position("R-b", 2, before_t0(key), ended_at=before_t0(key, 0), end_kind="moved", ended_after_run=1),
        _position("R-b", 3, before_t0(key, 0), inserted_after_run=1)])

    data = gather(_canonical(daemon_module, prefix, key, f"{key['subject']}-m1", []))

    assert data.session["description"].endswith(
        _shared_sentence("group 1 (probe R-a, montages 1, 2, every channel)")), data.session["description"]

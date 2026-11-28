"""The rig's console settings, through the builder (design spec
`2026-11-28-console-settings-reader-design.md`): each look's size, object
window and drawn chromaticity, each trial's training switch, the change log's
form, shaping and withdrawn kinds, a block the switch ended, and a run's color
sets -- and nothing of a withdrawal's stager but its kind. Subject and date
checked unclaimed across `tests/` on 2026-11-28, the date in the PAST, since
`nwbinspector` calls a future `session_start_time` critical."""

from __future__ import annotations

import json

import numpy as np
import pytest

from tests.schema.test_nwb_probes import _canonical, _position_each_probe
from tests.schema.test_spikeglx_restart import _session as _restart_session
from tests.synth.test_peripherals import CONSOLE_RIG, MIX_BLOCKS, MIX_MONTAGES


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def recorded(daemon_module, prefix, tmp_path_factory):
    """`test_nwb_rig_fields.py`'s three runs with the console settings shapes
    written (`CONSOLE_RIG`)."""
    recipe, key = _restart_session(
        tmp_path_factory, None, runs=True, subject="csnwb1", session_id="2025-07-03_01", blocks=MIX_BLOCKS,
        montages=MIX_MONTAGES, rig_record=CONSOLE_RIG)
    daemon_module.run_once(prefix=prefix)
    _position_each_probe(daemon_module, prefix, key)
    return recipe, key, _canonical(daemon_module, prefix, key, "csnwb1-k1", [])


@pytest.fixture(scope="module")
def built(recorded, tmp_path_factory):
    from wl_preproc.nwb.build import build

    return build(recorded[2], tmp_path_factory.mktemp("nwb-console-settings"))


def _file(built):
    from pynwb import NWBHDF5IO

    return NWBHDF5IO(str(built.path), "r")


def test_each_look_says_its_size_window_and_light(built):
    with _file(built) as io:
        module = io.read().processing["rig_record"]
        stimuli = module["stimuli"].to_dataframe()
        fix = stimuli[stimuli["stimulus"] == "fix"]
        assert fix["size_deg"].tolist() == [0.3] * len(fix)
        assert [tuple(value) for value in fix["light_xy"]] == [(0.3, 0.6)] * len(fix)
        target = stimuli[stimuli["stimulus"] == "target"]
        assert target["size_deg"].tolist() == [1.0] * len(target)
        assert all(np.isnan(value).all() and len(value) == 2 for value in target["light_xy"])
        objects = module["stimulus_objects"].to_dataframe()
        assert objects["size_deg"].tolist() == [2.4, 2.4] and objects["window_deg"].tolist() == [3.6, 3.6]
        assert [tuple(value) for value in objects["light_xy"]] == [(0.3, 0.6)] * 2
        tables = [stimuli, objects, io.read().trials.to_dataframe()]
        assert all("light_xyz_cd_m2" not in table.columns for table in tables)


def test_each_trial_says_its_training_switch(built):
    with _file(built) as io:
        trials = io.read().trials.to_dataframe()
        assert set(trials[trials["run_number"] == 3]["shaping"]) == {"full"}
        assert set(trials[trials["run_number"] != 3]["shaping"]) == {"on"}
        assert all(json.loads(value)["contrast"] == "full" for value in trials[trials["run_number"] == 3]["drawn"])


def test_the_change_log_knows_the_console_kinds(built):
    """Review Focus 1: a form, two switch changes and two withdrawals of one
    act, joined to strobes by code and never by kind; the stager's name and
    account are in no column."""
    with _file(built) as io:
        changes = io.read().processing["rig_record"]["parameter_changes"].to_dataframe()
        assert changes["kind"].tolist()[-5:] == ["form", "shaping", "shaping", "withdrawn", "withdrawn"]
        assert changes["change"].tolist()[-5:] == [9, 10, 11, 12, 12]
        assert changes["time_source"].tolist()[-5:] == ["code", "rig_clock", "rig_clock", "rig_clock", "rig_clock"]
        assert changes["staged_by_kind"].tolist() == [""] * (len(changes) - 2) + ["member", "member"]
        assert changes["moment"].tolist()[-3] == 0
        for column in changes.columns:
            if changes[column].dtype == object:
                text = " ".join(str(value) for value in changes[column])
                assert "Ada" not in text and "ada@lab" not in text, column
        assert "Ada" not in json.dumps(built.description) and "ada@lab" not in json.dumps(built.description)
        assert not [note for note in built.description["notes"] if "which no build of wl-xcon this reader knows" in note]


def test_a_block_ended_by_the_switch_and_the_runs_color_sets(built):
    with _file(built) as io:
        nwb = io.read()
        blocks = nwb.intervals["blocks"].to_dataframe()
        assert blocks["ended"].tolist() == ["rule", "shaping", "next"]
        runs = nwb.intervals["runs"].to_dataframe()
        sets = json.loads(runs["color_sets"].iloc[0])
        assert "macaque_row" in json.dumps(sets) and "design" in json.dumps(sets)
        varied = json.loads(blocks["varied"].iloc[0])
        assert varied["search_size"][0]["by"] == "scaled"


def test_a_form_is_a_persons_act(recorded, built):
    """The form is an act beside the set, the hand-back, the range and the
    revert: five, where the mix's own change log has four."""
    from wl_preproc.listing.entry import session_entry

    assert built.description["training_records"]["parameter_changes"] == 5
    assert session_entry(recorded[1])["training_records"]["parameter_changes"] == 5

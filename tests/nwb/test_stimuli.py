"""Where each stimulus a trial showed was, and when (design spec
`2026-11-07-rig-record-fields-design.md` section 4)."""

from __future__ import annotations

import math

from wl_preproc.events.rigtrials import Place, RigTrial, Stimulus


def _stimulus(name="target", at=Place(20.0, 20.0, 27.23631347517073, 45.0), **fields):
    return Stimulus(**{"name": name, "kind": "show", "frame": 85, "off": None, "changed": (), "drawn": True,
                       "unresolved": None, "at": at, **fields})


def _rig(number, stimuli):
    return RigTrial(number=number, index=0, outcome="correct", block="b", condition="c", params={},
                    stimuli=stimuli, carries=frozenset({"stimuli"}))


def test_one_row_per_entry_and_one_per_array_object():
    """Frames as frames (-1 for none), positions as numbers (NaN for none),
    an array's objects in their own rows, a per-eye array's by eye."""
    from wl_preproc.nwb.stimuli import stimulus_rows

    fix = _stimulus("fix", at=Place(0.0, 0.0, 0.0, None), frame=1, off=61)
    array = _stimulus("search", at=Place(0.0, 0.0, 0.0, None), target=1,
                      objects=(Place(8.0, 0.0, 8.0, 0.0), Place(-8.0, 0.0, 8.0, 180.0)))
    per_eye = _stimulus("dot", at=Place(), left={"x": 1.0}, eye_objects=(("left", (Place(2.0, 0.0, 2.0, 0.0),)),))
    blank = _stimulus("catch", drawn=False, kind="update", changed=("at", "opacity"))
    matched = {3: _rig(3, (fix, array)), 4: _rig(4, (per_eye, blank)), 5: _rig(5, None)}
    entries, objects, notes = stimulus_rows([{"trial_id": number} for number in (3, 4, 5, 6)], matched)
    assert [(row["trial_id"], row["entry"], row["stimulus"]) for row in entries] == [
        (3, 0, "fix"), (3, 1, "search"), (4, 0, "dot"), (4, 1, "catch")]
    assert (entries[0]["frame"], entries[0]["off"], entries[1]["off"]) == (1, 61, -1)
    assert math.isnan(entries[0]["polar_deg"]) and entries[0]["eccentricity_deg"] == 0.0
    assert (entries[1]["target"], entries[1]["n_objects"], entries[0]["target"]) == (1, 2, -1)
    assert math.isnan(entries[2]["x_deg"]) and entries[2]["left"] == '{"x": 1.0}' and entries[2]["right"] == ""
    assert (entries[3]["drawn"], entries[3]["kind"], entries[3]["changed"]) == (0, "update", "at,opacity")
    assert [(row["trial_id"], row["entry"], row["eye"], row["object"], row["x_deg"]) for row in objects] == [
        (3, 1, "", 0, 8.0), (3, 1, "", 1, -8.0), (4, 0, "left", 0, 2.0)]
    assert notes == []


def test_a_position_whose_angles_are_not_its_x_and_ys_is_noted_never_corrected():
    """One formula, two implementations: the record's eccentricity and polar
    angle are checked against `eye/detect/geometry.py`, past 1e-9 degrees. A
    polar angle a hair under 360 agrees with 0."""
    from wl_preproc.nwb.stimuli import stimulus_rows

    right = _stimulus("right", at=Place(8.0, -1e-13, 8.0, 0.0))
    wrong_size = _stimulus("off", at=Place(20.0, 20.0, 28.28, 45.0))
    wrong_angle = _stimulus("turned", at=Place(20.0, 20.0, 27.23631347517073, 44.0))
    centre = _stimulus("centre", at=Place(0.0, 0.0, 0.0, 90.0))
    unplaced = _stimulus("nowhere", at=Place(None, None, None, None), unresolved="setting 'x' has no value")
    matched = {1: _rig(1, (right, wrong_size)), 2: _rig(2, (wrong_angle, centre, unplaced))}
    entries, _objects, notes = stimulus_rows([{"trial_id": 1}, {"trial_id": 2}], matched)
    assert entries[1]["eccentricity_deg"] == 28.28
    assert notes == ["3 stimulus entries give an eccentricity or polar angle that is not its x and y's, past 1e-09 "
                     "degrees; published as written (trial 1 entry 1, trial 2 entry 0, trial 2 entry 1)"]


def test_a_per_eye_arrays_objects_are_counted_and_each_eyes_place_checked():
    """A stimulus drawn by per-eye positions counts both eyes' objects, as
    `stimulus_objects` lists them, and each eye's own position is checked
    against our geometry as a cyclopean one is (the rig record fields' final
    review, M4)."""
    from wl_preproc.nwb.stimuli import stimulus_rows

    dot = Place(2.0, 0.0, 2.0, 0.0)
    per_eye = _stimulus("dot", at=Place(), left={"x": 1.0}, right={"x": -1.0},
                        eye_at=(("left", Place(1.0, 0.0, 1.0, 0.0)), ("right", Place(-1.0, 0.0, 1.0, 90.0))),
                        eye_objects=(("left", (dot, dot)), ("right", (dot,))))
    entries, objects, notes = stimulus_rows([{"trial_id": 4}], {4: _rig(4, (per_eye,))})
    assert entries[0]["n_objects"] == len(objects) == 3
    assert notes == ["1 stimulus entry give an eccentricity or polar angle that is not its x and y's, past 1e-09 "
                     "degrees; published as written (trial 4 entry 0)"]


def test_the_size_window_and_light_are_columns_where_some_entry_carries_them():
    """A session some entry carries the console's keys in gains the columns
    on every row; an array's own entry has none of its own."""
    from wl_preproc.events.rigtrials import AsDrawn
    from wl_preproc.nwb.stimuli import stimulus_rows

    fix = _stimulus("fix", as_drawn=AsDrawn(0.3, None, (0.3, 0.6)), carries=frozenset({"size_deg", "light_xy"}))
    array = _stimulus("search", at=Place(0.0, 0.0, 0.0, None), target=0,
                      objects=(Place(8.0, 0.0, 8.0, 0.0), Place(-8.0, 0.0, 8.0, 180.0)),
                      objects_as_drawn=(AsDrawn(2.4, 3.6, (0.3, 0.6)), AsDrawn(2.4)))
    matched = {1: _rig(1, (fix, array)), 2: _rig(2, ())}
    entries, objects, _notes = stimulus_rows([{"trial_id": 1}, {"trial_id": 2}, {"trial_id": 3}], matched)
    assert [row["trial_id"] for row in entries] == [1, 1]
    assert entries[0]["size_deg"] == 0.3 and entries[0]["light_xy"] == [0.3, 0.6]
    assert math.isnan(entries[1]["size_deg"]) and len(entries[1]["light_xy"]) == 2
    assert all(math.isnan(value) for value in entries[1]["light_xy"])
    assert [row["window_deg"] for row in objects][0] == 3.6 and math.isnan(objects[1]["window_deg"])
    assert [row["size_deg"] for row in objects] == [2.4, 2.4]
    assert objects[0]["light_xy"] == [0.3, 0.6]
    assert all(math.isnan(value) for value in objects[1]["light_xy"]) and len(objects[1]["light_xy"]) == 2


def test_a_file_no_entry_carries_them_in_has_todays_rows():
    """A record written before the console build: no row gains a key."""
    from wl_preproc.nwb.stimuli import stimulus_rows

    array = _stimulus("search", at=Place(0.0, 0.0, 0.0, None), objects=(Place(8.0, 0.0, 8.0, 0.0),))
    per_eye = _stimulus("dot", eye_objects=(("left", (Place(2.0, 0.0, 2.0, 0.0),)),))
    entries, objects, _notes = stimulus_rows([{"trial_id": 1}], {1: _rig(1, (_stimulus("fix"), array, per_eye))})
    assert {key for row in entries for key in row} == {
        "trial_id", "entry", "stimulus", "kind", "frame", "off", "changed", "drawn", "unresolved", "x_deg", "y_deg",
        "eccentricity_deg", "polar_deg", "target", "n_objects", "left", "right"}
    assert {key for row in objects for key in row} == {
        "trial_id", "entry", "eye", "object", "x_deg", "y_deg", "eccentricity_deg", "polar_deg"}
    assert len(objects) == 2


def test_a_per_eye_arrays_objects_carry_their_sizes_in_their_rows():
    """Two objects in each eye, every value different: each row holds its own
    object's size, window and light, by (eye, object)."""
    from wl_preproc.events.rigtrials import AsDrawn
    from wl_preproc.nwb.stimuli import stimulus_rows

    left = (Place(2.0, 0.0, 2.0, 0.0), Place(3.0, 0.0, 3.0, 0.0))
    right = (Place(-2.0, 0.0, 2.0, 180.0), Place(-3.0, 0.0, 3.0, 180.0))
    per_eye = _stimulus("dot", at=Place(), eye_objects=(("left", left), ("right", right)),
                        eye_objects_as_drawn=(
                            ("left", (AsDrawn(1.0, 1.5, (0.1, 0.2)), AsDrawn(1.1, 1.6, (0.11, 0.21)))),
                            ("right", (AsDrawn(2.0, 2.5, (0.3, 0.4)), AsDrawn(2.1, 2.6, (0.31, 0.41))))),
                        carries=frozenset({"size_deg", "window_deg", "light_xy"}))
    _entries, objects, _notes = stimulus_rows([{"trial_id": 1}], {1: _rig(1, (per_eye,))})
    assert {(row["eye"], row["object"]): (row["x_deg"], row["size_deg"], row["window_deg"], row["light_xy"])
            for row in objects} == {
        ("left", 0): (2.0, 1.0, 1.5, [0.1, 0.2]), ("left", 1): (3.0, 1.1, 1.6, [0.11, 0.21]),
        ("right", 0): (-2.0, 2.0, 2.5, [0.3, 0.4]), ("right", 1): (-3.0, 2.1, 2.6, [0.31, 0.41])}

from wl_preproc.montage.derive import DerivedMontage, DerivedProbe
from wl_preproc.montage.groups import DerivedGroup, shared_groups

A = frozenset(range(8))
B = frozenset({0, 1, 2, 3, 8, 9, 10, 11})
ELECTRODES = {"A": A, "B": B}


def mont(mid, *probes):
    return DerivedMontage(mid, float(mid), float(mid + 1), (mid,), tuple(probes))


def probe(h, runs, serial="S1", insertion=1):
    return DerivedProbe(serial, insertion, h, runs)


def test_a_b_a_at_one_position():
    ms = [mont(1, probe("A", (1,))), mont(2, probe("B", (2,))), mont(3, probe("A", (3,)))]
    assert shared_groups(ms, ELECTRODES) == (
        DerivedGroup(1, "S1", 1, (1, 2, 3), (0, 1, 2, 3), ("A", "B", "A"), (1, 2, 3)),
        DerivedGroup(2, "S1", 1, (1, 3), tuple(range(8)), ("A", "A"), (1, 3)),
    )


def test_a_probe_that_never_changed_has_one_group_over_its_position():
    other = lambda r: DerivedProbe("S2", 1, "B", (r,))  # noqa: E731
    ms = [mont(1, probe("A", (1,)), other(1)), mont(2, probe("A", (2,)), other(2))]
    groups = [g for g in shared_groups(ms, ELECTRODES) if g.serial == "S1"]
    assert groups == [DerivedGroup(1, "S1", 1, (1, 2), tuple(range(8)), ("A", "A"), (1, 2))]


def test_a_move_ends_sharing():
    ms = [mont(1, probe("A", (1,), insertion=1)), mont(2, probe("A", (2,), insertion=2))]
    assert shared_groups(ms, ELECTRODES) == ()


def test_a_montage_with_no_sortable_run_is_no_member():
    ms = [mont(1, probe("A", (1,))), mont(2, probe("A", ())), mont(3, probe("A", (3,)))]
    (g,) = shared_groups(ms, ELECTRODES)
    assert g.montage_ids == (1, 3) and g.sortable_runs == (1, 3)


def test_a_probe_without_a_channel_map_shares_every_channel():
    ms = [mont(1, probe(None, (1,), "INTAN-1", 3)), mont(2, probe(None, (2,), "INTAN-1", 3))]
    (g,) = shared_groups(ms, {})
    assert g.electrodes is None and g.member_hashes == () and g.montage_ids == (1, 2)


def test_groups_are_numbered_by_insertion_then_longest_first():
    ms = [
        mont(1, probe("A", (1,), "S1", 2), probe("A", (1,), "S2", 1)),
        mont(2, probe("B", (2,), "S1", 2), probe("B", (2,), "S2", 1)),
        mont(3, probe("A", (3,), "S1", 2), probe("A", (3,), "S2", 1)),
    ]
    got = [(g.group_id, g.serial, g.insertion_number, g.montage_ids) for g in shared_groups(ms, ELECTRODES)]
    assert got == [
        (1, "S2", 1, (1, 2, 3)), (2, "S2", 1, (1, 3)), (3, "S1", 2, (1, 2, 3)), (4, "S1", 2, (1, 3))]


def test_two_electrodes_are_a_group():
    ms = [mont(1, probe("A", (1,))), mont(2, probe("C", (2,)))]
    (g,) = shared_groups(ms, {"A": A, "C": frozenset({6, 7, 20})})
    assert g.electrodes == (6, 7)

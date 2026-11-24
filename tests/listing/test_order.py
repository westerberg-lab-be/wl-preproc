"""What ends before it starts, and how it is named, for the listing and the
build alike (design spec `2026-11-24-wl-works-requests-design.md` section
4.5)."""

from __future__ import annotations

import pytest


def test_equal_times_do_not_end_before_start():
    from wl_preproc.listing.order import ends_before_start

    assert ends_before_start(5.0, 5.0) is False
    assert ends_before_start(5.0, 4.999) is True
    assert ends_before_start(5.0, 5.001) is False


@pytest.mark.parametrize("where", ["listing", "file"])
def test_the_messages(where):
    from wl_preproc.listing.order import block_message, run_message, segment_message

    tail = f"left out of this {where}"
    assert run_message(3, 101.3, 101.25, where=where) == (
        f"run 3 ends at 101.250 s, before its start at 101.300 s: {tail}")
    assert block_message(4, 3, 50.0, 49.5, where=where) == (
        f"block 4 of run 3 ends at 49.500 s, before its start at 50.000 s: {tail}")
    assert block_message(4, None, 50.0, 49.5, where=where) == (
        f"block 4 ends at 49.500 s, before its start at 50.000 s: {tail}")
    assert segment_message("run_g0/run_g0_t0.nidq.bin", 7.0, 6.0, where=where) == (
        f"file run_g0/run_g0_t0.nidq.bin ends at 6.000 s, before its start at 7.000 s: {tail}")


def test_a_message_for_anywhere_else_is_refused():
    from wl_preproc.listing.order import block_message, run_message, segment_message

    for message in (lambda: run_message(3, 2.0, 1.0, where="nwb"),
                    lambda: block_message(4, 3, 2.0, 1.0, where="Listing"),
                    lambda: segment_message("a.bin", 2.0, 1.0, where="")):
        with pytest.raises(ValueError, match="listing.*file"):
            message()

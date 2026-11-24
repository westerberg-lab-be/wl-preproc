"""What ends before it starts, and how it is named (design spec
`2026-11-24-wl-works-requests-design.md` section 4.5).

**One rule for the listing, `accept()` and the build.** A run, block or file
whose end comes before its start is left out of the listing and out of the
file, and named, with both its times, where it was left out. Equal start and
end is never before. `where` says which: `"listing"` or `"file"`.
"""

from __future__ import annotations

_WHERE = ("listing", "file")


def ends_before_start(start_s: float, end_s: float) -> bool:
    """True when `end_s` comes before `start_s`; equal times do not."""
    return end_s < start_s


def run_message(run_number: int, start_s: float, end_s: float, *, where: str) -> str:
    return _message(f"run {run_number}", start_s, end_s, where)


def block_message(block_number: int, run_number: int | None, start_s: float, end_s: float, *, where: str) -> str:
    """The block named by its run's number too, when it lies in one."""
    item = f"block {block_number}" if run_number is None else f"block {block_number} of run {run_number}"
    return _message(item, start_s, end_s, where)


def segment_message(segment_id: str, start_s: float, end_s: float, *, where: str) -> str:
    return _message(f"file {segment_id}", start_s, end_s, where)


def _message(item: str, start_s: float, end_s: float, where: str) -> str:
    if where not in _WHERE:
        raise ValueError(f"where is 'listing' or 'file', not {where!r}")
    return f"{item} ends at {end_s:.3f} s, before its start at {start_s:.3f} s: left out of this {where}"

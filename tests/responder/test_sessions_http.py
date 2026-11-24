"""`GET /sessions` over real HTTP, with a stand-in callable (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1). The
database half is in `tests/schema/test_session_listing.py`."""

from __future__ import annotations

import datetime
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.responder.test_http import TOKEN, _health_ok, _request, _unused
from wl_preproc.responder.handler import make_handler


@pytest.fixture
def serve_sessions():
    started = []

    def _start(sessions_list_fn=_unused, *, with_sessions=True):
        handler_cls = (make_handler(TOKEN, _health_ok, _unused, sessions_list_fn=sessions_list_fn)
                       if with_sessions else make_handler(TOKEN, _health_ok, _unused))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield _start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_the_listing_is_behind_the_token_and_takes_an_optional_cursor(serve_sessions):
    calls = []
    base = serve_sessions(lambda since, limit: calls.append((since, limit)) or {"cursor": 3, "sessions": []})
    assert _request(f"{base}/sessions", method="GET")[0] == 401
    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (200, {"cursor": 3, "sessions": []})
    assert _request(f"{base}/sessions?since=2", method="GET", token=TOKEN)[0] == 200
    assert calls == [(None, None), (2, None)]


@pytest.mark.parametrize("query", ["since=x", "since=-1", "since=1&since=2", "other=1", "since="])
def test_a_cursor_that_is_not_one_non_negative_integer_is_422(serve_sessions, query):
    base = serve_sessions()
    status, body = _request(f"{base}/sessions?{query}", method="GET", token=TOKEN)
    assert status == 422 and "since" in json.loads(body)["error"]


def test_a_host_without_the_listing_does_not_answer_it(serve_sessions):
    base = serve_sessions(with_sessions=False)
    assert _request(f"{base}/sessions", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{base}/sessions?since=1", method="GET", token=TOKEN)[0] == 404


def test_only_get_answers_it_and_a_query_elsewhere_is_still_404(serve_sessions):
    base = serve_sessions(lambda since, limit: {"cursor": 0, "sessions": []})
    assert _request(f"{base}/sessions", method="POST", token=TOKEN, body={})[0] == 405
    assert _request(f"{base}/health?since=1", method="GET", token=TOKEN)[0] == 404


def test_a_failure_behind_it_is_a_clean_500(serve_sessions):
    def broken(*_args):
        raise RuntimeError("the database went away")

    status, body = _request(f"{serve_sessions(broken)}/sessions", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (500, {"error": "RuntimeError: the database went away"})


def test_get_sessions_lists_the_task_hash(serve_sessions, dj_conn, prefix, tmp_path_factory):
    """One landed session, through HTTP: each run lists the hash of the task
    file it ran (the synthetic rig's start rows carry one)."""
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon
    from wl_preproc.responder.sessions import list_sessions

    daemon.activate_all(prefix=prefix)
    _recipe, key = _session(tmp_path_factory, subject="slhttp1", session_id="2025-08-01_01", runs=True,
                                 rig_record={"session_kind": "recording", "fields": True})
    daemon.run_once(prefix=prefix)
    base = serve_sessions(lambda since, limit: list_sessions(since, limit, prefix=prefix))
    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert status == 200
    (entry,) = [entry for entry in json.loads(body)["sessions"] if entry["subject"] == key["subject"]]
    assert [run["task_hash"] for run in entry["runs"]] == ["sha256:synthetic", "sha256:synthetic"]


def test_get_sessions_lists_t0s_wall_time_as_stored(serve_sessions, dj_conn, prefix, tmp_path_factory):
    """`t0_datetime` and `t0_source` are the stored row's
    (`timebase.ClockReference`), and both null for a session whose t = 0 is
    not stored yet."""
    from tests.identities import new_animal
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import timebase

    daemon.activate_all(prefix=prefix)
    session = new_animal().session()
    _recipe, key = _session(tmp_path_factory, subject=session.subject, session_id=session.session_id,
                            session_datetime=session.session_datetime, runs=True)
    daemon.run_once(prefix=prefix)
    base = serve_sessions(lambda since, limit: list_sessions(since, limit, prefix=prefix))

    def listed():
        status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
        assert status == 200
        (entry,) = [entry for entry in json.loads(body)["sessions"] if entry["subject"] == key["subject"]]
        return entry["t0_datetime"], entry["t0_source"]

    row = (timebase.ClockReference & key).fetch1()
    assert listed() == (row["reference_time"].isoformat(), row["reference_source"])
    (timebase.ClockReference & key).delete_quick()
    try:
        assert listed() == (None, None)
    finally:
        timebase.ClockReference.insert1(row, allow_direct_insert=True)


def test_one_reversed_session_does_not_break_the_page(serve_sessions, dj_conn, prefix, tmp_path_factory):
    """A run, a block and a file that end before they start, written into
    `core` after the session landed, are left out of its entry and flagged
    (design spec `2026-11-24-wl-works-requests-design.md` section 4.5). The
    page still answers, and lists the other session whole."""
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import core

    daemon.activate_all(prefix=prefix)
    _recipe, bad = _session(tmp_path_factory, subject="slhttp2", session_id="2025-08-04_01", runs=True,
                            rig_record={"session_kind": "recording", "fields": True})
    _recipe, good = _session(tmp_path_factory, subject="slhttp3", session_id="2025-08-05_01", runs=True,
                             rig_record={"session_kind": "recording", "fields": True})
    daemon.run_once(prefix=prefix)
    base = serve_sessions(lambda since, limit: list_sessions(since, limit, prefix=prefix))
    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert status == 200
    before = {entry["subject"]: entry for entry in json.loads(body)["sessions"]}

    first_run, second_run = (core.Run & bad).to_dicts(order_by="run_number")
    core.Run.update1({**first_run, "run_stop_time": first_run["run_start_time"] - 0.25})
    block = (core.BlockTime & bad & f"start_s > {second_run['run_start_time']}").to_dicts(order_by="start_s")[0]
    core.BlockTime.update1({**block, "stop_s": block["start_s"] - 0.5})
    (segment,) = (core.Segment & bad & {"system": "spikeglx"}).to_dicts()
    core.Segment.update1({**segment, "end_s": segment["start_s"] - 1.0})

    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert status == 200
    after = {entry["subject"]: entry for entry in json.loads(body)["sessions"]}
    assert after[good["subject"]] == before[good["subject"]]
    entry = after[bad["subject"]]
    assert [run["run_number"] for run in entry["runs"]] == [second_run["run_number"]]
    assert block["block_id"] not in [listed["block_number"] for listed in entry["runs"][0]["blocks"]]
    assert entry["segments"] == [] and entry["runs"][0]["segments"] == []
    withheld = [(flag["run_number"], flag["block_number"], flag["segment_id"])
                for flag in entry["flags"] if flag["code"] == "ends_before_start"]
    assert withheld == [(None, None, segment["segment_id"]), (first_run["run_number"], None, None),
                        (second_run["run_number"], block["block_id"], None)]


def _land_five(tmp_path_factory, subject):
    from tests.schema.test_spikeglx_restart import _session

    return [_session(tmp_path_factory, subject=f"{subject}{number}", session_id=f"2025-09-0{number + 1}_01")[1]
            for number in range(5)]


def _page(base, since, limit):
    status, body = _request(f"{base}/sessions?since={since}&limit={limit}", method="GET", token=TOKEN)
    assert status == 200
    return json.loads(body)


def test_sessions_page_by_limit(serve_sessions, dj_conn, prefix, tmp_path_factory):
    """Five changed sessions and `limit=2` give pages of 2, 2 and 1. Each cursor is the page's latest
    `change_seq`; the short page ends it; an empty page's cursor is `since` (design spec
    `2026-11-24-wl-works-requests-design.md` section 4.4)."""
    from wl_preproc import daemon
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import ingest

    daemon.activate_all(prefix=prefix)
    floor = int(max(list(ingest.SessionChange.to_arrays("change_seq")) or [0]))
    keys = _land_five(tmp_path_factory, "slpage")
    daemon.run_once(prefix=prefix)
    base = serve_sessions(lambda since, limit: list_sessions(since, limit, prefix=prefix))
    # Other tests' sessions that the pass above relisted are on the pages too.
    latest = {}
    for row in ingest.SessionChange & f"change_seq > {floor}":
        latest[row["subject"]] = max(latest.get(row["subject"], 0), row["change_seq"])
    order = sorted(latest, key=latest.get)
    assert {key["subject"] for key in keys} <= set(order)
    sizes = [2] * (len(order) // 2) + [len(order) % 2] * (len(order) % 2)

    since, pages = floor, []
    for expected in sizes:
        page = _page(base, since, 2)
        pages.append([entry["subject"] for entry in page["sessions"]])
        assert len(pages[-1]) == expected and page["cursor"] == max(latest[subject] for subject in pages[-1])
        since = page["cursor"]
    assert [subject for page in pages for subject in page] == order
    assert _page(base, since, 2) == {"cursor": since, "sessions": []}


def test_a_session_changed_between_pages_is_not_lost(serve_sessions, dj_conn, prefix, tmp_path_factory):
    """One listed on page 1 that changes before page 2 appears on a later page."""
    from wl_preproc import daemon
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import ingest

    daemon.activate_all(prefix=prefix)
    floor = int(max(list(ingest.SessionChange.to_arrays("change_seq")) or [0]))
    keys = _land_five(tmp_path_factory, "slmove")
    daemon.run_once(prefix=prefix)
    base = serve_sessions(lambda since, limit: list_sessions(since, limit, prefix=prefix))
    first = _page(base, floor, 2)
    listed = [entry["subject"] for entry in first["sessions"]]
    moved = (ingest.SessionChange & {"subject": listed[0]}).proj("subject", "session_datetime").to_dicts()[0]
    moved = {name: moved[name] for name in ("subject", "session_datetime")}
    ingest.SessionChange.insert1({**moved, "digest": "0" * 64, "changed_at": datetime.datetime(2027, 6, 1)},
                                 ignore_extra_fields=True)
    seen, since = list(listed), first["cursor"]
    while True:
        page = _page(base, since, 2)
        if not page["sessions"]:
            break
        seen += [entry["subject"] for entry in page["sessions"]]
        since = page["cursor"]
    assert seen.count(listed[0]) == 2 and {key["subject"] for key in keys} <= set(seen)


def test_only_the_page_is_built(serve_sessions, dj_conn, prefix, tmp_path_factory, monkeypatch):
    from wl_preproc import daemon
    from wl_preproc.listing import entry
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import ingest

    daemon.activate_all(prefix=prefix)
    floor = int(max(list(ingest.SessionChange.to_arrays("change_seq")) or [0]))
    _land_five(tmp_path_factory, "slbuilt")
    daemon.run_once(prefix=prefix)
    built, gathered = [], []
    real_build, real_gather = entry.build_entry, entry.gather_all
    monkeypatch.setattr(entry, "build_entry", lambda facts: built.append(facts) or real_build(facts))
    monkeypatch.setattr(entry, "gather_all", lambda keys: gathered.append(len(keys)) or real_gather(keys))
    base = serve_sessions(lambda since, limit: list_sessions(since, limit, prefix=prefix))
    assert len(_page(base, floor, 2)["sessions"]) == 2
    assert len(built) == 2 and gathered == [2]
